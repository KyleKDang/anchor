"""Regenerating the taste profile's numeric artifacts from the ordering.

Two of the profile's three artifacts live here - the weight vector and the exemplar set -
and both are *regenerated, never patched* (ADR 0004). Every ordering change throws the
old fit away and trains a new one from scratch, which costs milliseconds at library
scale and buys the one property an incremental scheme cannot: the vector is always
exactly what the current ordering implies, with no drift between the two to reason about.

Everything here is read-only on the ordering and the anchor marks. This module is the
advisory math (ADR 0001, ADR 0013): it reads what the owner's own acts settled and writes
only its own artifacts, and nothing it computes may move a film's band, its rank, or its
mark. Nothing it computes reaches a surface either - a film's score is rating-shaped, and
ADR 0005 keeps that off every response for an unwatched film.

Each retrain also appends one metrics row (evaluation.md): held-out pairwise accuracy
over the cross-band pairs and the owner's own band comparisons, plus the counts that say
what that accuracy is accuracy *of*. It is the fast signal - the honest one, landings in
the ordering, takes months - and it is append-only, so a scorer that degrades when the
feature set changes shows up as a step in a column rather than as nothing at all.
"""

import uuid
import zlib
from collections.abc import Sequence

import numpy as np
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from anchor import anchors as anchors_module
from anchor import features, readiness, trainer
from anchor import ordering as ordering_module
from anchor.models import (
    BANDS,
    ComparisonKind,
    ComparisonLogEntry,
    ComparisonVerdict,
    Exemplar,
    ExemplarRole,
    Film,
    TasteMetrics,
    WeightVector,
)
from anchor.ordering import Ordering
from anchor.trainer import Answered

HELD_OUT_SHARE = 0.2
"""How much of the eligible evidence is kept back to measure against."""

EXTREMES = 3
"""Films from each end of the ordering that stand for the owner's taste alongside the
anchors. Three: enough that one unusual favourite does not speak for the whole top."""

ANCHORS_PER_BAND = 3
"""How many of a band's anchors stand for it, where the pool has grown past that.

The spec's "a few per band" (taste-profile.md). The cap exists because a prompt carries
a handful of names, not a hundred, and the pool it draws from is the owner's own most
recent certainty - so the newest marks are the ones kept.
"""


async def retrain(db: AsyncSession, account_id: uuid.UUID) -> None:
    """Rebuild this account's weight vector and exemplar set, and record how it did.

    The measured fit and the stored fit are deliberately two fits. Held-out accuracy only
    means anything if the vector being measured never saw the held-out answers, while the
    vector the app *uses* should be trained on everything the owner has said - so the
    slice is held back to measure, and put back to train the artifact.
    """
    ordering = await ordering_module.load(db, account_id)
    films = await _films(db, ordering.all_film_ids())
    seed = _seed(account_id, ordering)
    answered = await _band_comparisons(db, account_id)
    pairs = trainer.extract(ordering, seed=seed, explicit=answered)
    held_out, training = trainer.hold_out(pairs, share=HELD_OUT_SHARE, seed=seed)

    space = features.learn(list(films.values()))
    measured = trainer.fit(trainer.design(training, space, films))
    accuracy = trainer.accuracy(measured, trainer.design(held_out, space, films))
    weights = trainer.fit(trainer.design(pairs, space, films)) if held_out else measured

    await _store_vector(db, account_id, space, weights, len(pairs))
    await _store_exemplars(db, account_id, ordering, await anchors_module.pools(db, account_id))
    db.add(
        _metrics_row(
            account_id,
            evidence=await readiness.evidence(db, account_id),
            band_comparisons=len(answered),
            accuracy=accuracy,
            held_out=len(held_out),
            training=len(training),
        )
    )


def _seed(account_id: uuid.UUID, ordering: Ordering) -> int:
    """The sampling seed: fixed per account and library size, so a retrain is reproducible.

    Per account so two owners never share a sample, and per size so the long-range draw
    refreshes as the library grows - a sample frozen for the life of an account could
    stay quietly unlucky forever.
    """
    return zlib.crc32(account_id.bytes) + len(ordering)


# --- The stored artifacts ---


async def _store_vector(
    db: AsyncSession,
    account_id: uuid.UUID,
    space: features.FeatureSpace,
    weights: np.ndarray,
    training_pairs: int,
) -> None:
    """Replace the account's fit. Current-only, so there is one row and it is overwritten.

    ``trained_at`` is written on the update as well as the insert. Left to its server
    default it would record when the account *first* trained and never move again, which
    is the one thing the marker exists not to say.
    """
    values = {
        "weights": {
            column: float(weight) for column, weight in zip(space.columns, weights, strict=True)
        },
        "space": space.to_json(),
        "training_pairs": training_pairs,
        "trained_at": func.now(),
    }
    await db.execute(
        insert(WeightVector)
        .values(id=uuid.uuid4(), account_id=account_id, **values)
        .on_conflict_do_update(index_elements=[WeightVector.account_id], set_=values)
    )


async def _store_exemplars(
    db: AsyncSession,
    account_id: uuid.UUID,
    ordering: Ordering,
    pools: dict[float, list[int]],
) -> None:
    """Recompute the whole set: the anchors, and the films at either end of the ordering.

    Wholesale rather than diffed, because the set is a mechanical reading of two things
    that both just moved, and a row left behind from the last reading would be a film
    still standing for a taste it no longer represents.

    A band may hold any number of anchors now, so a large pool is capped to
    :data:`ANCHORS_PER_BAND`, most recently marked first - which is the order
    :func:`anchor.anchors.pools` hands them over in. ``rank`` runs across the whole
    anchor role, best band first and newest mark first inside a band, so a prompt that
    reads the first few names gets the owner's best certainties.
    """
    await db.execute(delete(Exemplar).where(Exemplar.account_id == account_id))
    ranked = ordering.all_film_ids()
    rows = [
        Exemplar(
            account_id=account_id,
            film_id=film_id,
            role=ExemplarRole.anchor,
            band=band,
            rank=rank,
        )
        for rank, (band, film_id) in enumerate(
            (band, film_id) for band in BANDS for film_id in pools.get(band, ())[:ANCHORS_PER_BAND]
        )
    ]
    rows += _extremes(account_id, len(ranked), ExemplarRole.best, ranked[:EXTREMES])
    rows += _extremes(
        account_id, len(ranked), ExemplarRole.worst, list(reversed(ranked))[:EXTREMES]
    )
    db.add_all(rows)


def _extremes(
    account_id: uuid.UUID, rated: int, role: ExemplarRole, films: Sequence[int]
) -> list[Exemplar]:
    """One end of the ordering, closest to the end first, and never the same film twice.

    A library shorter than twice :data:`EXTREMES` would otherwise have its middle films
    standing for both ends at once, which says nothing about anything.
    """
    keep = films if rated >= 2 * EXTREMES else films[: rated // 2]
    return [
        Exemplar(account_id=account_id, film_id=film_id, role=role, band=None, rank=rank)
        for rank, film_id in enumerate(keep)
    ]


def _metrics_row(
    account_id: uuid.UUID,
    *,
    evidence: readiness.Evidence,
    band_comparisons: int,
    accuracy: float | None,
    held_out: int,
    training: int,
) -> TasteMetrics:
    """One retrain, as the append-only log records it.

    ``training`` is the fit the accuracy was earned on - the pairs minus the held-out
    slice - so the two counts partition the evidence rather than overlapping. The stored
    vector sees more than this, and carries its own count.
    """
    return TasteMetrics(
        account_id=account_id,
        held_out_accuracy=accuracy,
        held_out_pairs=held_out,
        training_pairs=training,
        rated_films=evidence.rated_films,
        bands_spanned=evidence.bands_spanned,
        band_comparisons=band_comparisons,
    )


# --- Reads ---


async def _films(db: AsyncSession, film_ids: Sequence[int]) -> dict[int, Film]:
    """The catalog rows behind the ordering: the only thing the features are read from."""
    if not film_ids:
        return {}
    rows = await db.scalars(select(Film).where(Film.tmdb_id.in_(film_ids)))
    return {film.tmdb_id: film for film in rows}


async def _band_comparisons(db: AsyncSession, account_id: uuid.UUID) -> list[Answered]:
    """Every band comparison the owner answered a direction to, as they answered it.

    A skip is the owner declining to judge, so it says nothing. "About the same" is not a
    direction either, and there are no ties in the ordering to point it at - what it
    settled is the band, and the band pick that followed it already records that.

    ``film_a`` is always the film being rated and ``film_b`` the one it was set against,
    so the verdict reads directly: ``a`` means the subject won. Whether the ordering still
    agrees is the trainer's question, not this one's.
    """
    rows = await db.execute(
        select(
            ComparisonLogEntry.film_a_id,
            ComparisonLogEntry.film_b_id,
            ComparisonLogEntry.verdict,
        ).where(
            ComparisonLogEntry.account_id == account_id,
            ComparisonLogEntry.kind == ComparisonKind.band_comparison,
            ComparisonLogEntry.verdict.in_((ComparisonVerdict.a, ComparisonVerdict.b)),
            ComparisonLogEntry.film_b_id.is_not(None),
        )
    )
    return [
        Answered(better=a, worse=b)
        if verdict is ComparisonVerdict.a
        else Answered(better=b, worse=a)
        for a, b, verdict in rows
    ]
