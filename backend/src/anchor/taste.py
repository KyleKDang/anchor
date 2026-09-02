"""Regenerating the taste profile's numeric artifacts from the ordering.

Two of the profile's three artifacts live here - the weight vector and the exemplar set -
and both are *regenerated, never patched* (ADR 0004). Every ordering change throws the
old fit away and trains a new one from scratch, which costs milliseconds at library
scale and buys the one property an incremental scheme cannot: the vector is always
exactly what the current ordering implies, with no drift between the two to reason about.

Everything here is read-only on the ordering, the dividers, and the anchors. This module
is the advisory math (ADR 0001): it reads what the owner's judgments settled and writes
only its own artifacts, and nothing it computes may move a film, a divider, or a
designation. Nothing it computes reaches a surface either - a film's score is
rating-shaped, and ADR 0005 keeps that off every response for an unwatched film.

Each retrain also appends one metrics row (evaluation.md): held-out pairwise accuracy
over the comparisons the owner actually answered, plus the counts that say what that
accuracy is accuracy *of*. It is the fast signal - the honest one, landings in the
ordering, takes months - and it is append-only, so a scorer that degrades when the
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
from anchor import bands, features, readiness, trainer
from anchor import ordering as ordering_module
from anchor.models import (
    AccountFilm,
    ComparisonKind,
    ComparisonLogEntry,
    ComparisonStatus,
    ComparisonVerdict,
    Exemplar,
    ExemplarRole,
    Film,
    Placement,
    PlacementTrust,
    TasteMetrics,
    WeightVector,
)
from anchor.ordering import Ordering

HELD_OUT_SHARE = 0.2
"""How much of the owner's answered comparisons is kept back to measure against."""

EXTREMES = 3
"""Films from each end of the ordering that stand for the owner's taste alongside the
anchors. Three: enough that one unusual favourite does not speak for the whole top."""


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
    pairs = trainer.extract(
        ordering,
        seed=seed,
        explicit=await _answered_pairs(db, account_id),
        provisional=await _provisional(db, account_id),
    )
    held_out, training = trainer.hold_out(pairs, share=HELD_OUT_SHARE, seed=seed)

    space = features.learn(list(films.values()))
    measured = trainer.fit(trainer.design(training, space, films))
    accuracy = trainer.accuracy(measured, trainer.design(held_out, space, films))
    weights = trainer.fit(trainer.design(pairs, space, films)) if held_out else measured

    await _store_vector(db, account_id, space, weights, len(pairs))
    await _store_exemplars(db, account_id, ordering, await anchors_module.current(db, account_id))
    db.add(
        _metrics_row(
            account_id,
            evidence=await readiness.evidence(db, account_id),
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
    anchors: dict[float, int],
) -> None:
    """Recompute the whole set: the anchors, and the films at either end of the ordering.

    Wholesale rather than diffed, because the set is a mechanical reading of two things
    that both just moved, and a row left behind from the last reading would be a film
    still standing for a taste it no longer represents.
    """
    await db.execute(delete(Exemplar).where(Exemplar.account_id == account_id))
    ranked = ordering.all_film_ids()
    rows = [
        Exemplar(
            account_id=account_id,
            film_id=film_id,
            role=ExemplarRole.anchor,
            band=band,
            rank=bands.rank(band),
        )
        for band, film_id in sorted(anchors.items(), reverse=True)
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
        explicit_comparisons=evidence.explicit_comparisons,
        settled_films=evidence.settled_films,
        bands_spanned=evidence.bands_spanned,
    )


# --- Reads ---


async def _films(db: AsyncSession, film_ids: Sequence[int]) -> dict[int, Film]:
    """The catalog rows behind the ordering: the only thing the features are read from."""
    if not film_ids:
        return {}
    rows = await db.scalars(select(Film).where(Film.tmdb_id.in_(film_ids)))
    return {film.tmdb_id: film for film in rows}


async def _answered_pairs(db: AsyncSession, account_id: uuid.UUID) -> set[frozenset[int]]:
    """Every pair of films the owner actually judged against each other.

    Active entries only, and never a skip: a skip is the owner declining to judge, which
    is the absence of evidence rather than evidence of equality. Which way each pair goes
    is read off the ordering, not off the answer - the log is evidence about the primary
    state, not the state itself (ADR 0010).
    """
    rows = await db.execute(
        select(ComparisonLogEntry.film_a_id, ComparisonLogEntry.film_b_id).where(
            ComparisonLogEntry.account_id == account_id,
            ComparisonLogEntry.kind == ComparisonKind.overall,
            ComparisonLogEntry.status == ComparisonStatus.active,
            ComparisonLogEntry.verdict != ComparisonVerdict.skip,
            ComparisonLogEntry.film_b_id.is_not(None),
        )
    )
    return {frozenset({a, b}) for a, b in rows}


async def _provisional(db: AsyncSession, account_id: uuid.UUID) -> set[int]:
    """Films whose position Anchor guessed rather than the owner settled."""
    rows = await db.scalars(
        select(AccountFilm.film_id)
        .select_from(Placement)
        .join(AccountFilm, AccountFilm.id == Placement.account_film_id)
        .where(Placement.account_id == account_id, Placement.trust == PlacementTrust.provisional)
    )
    return set(rows)
