"""Onboarding: the entry fork, and one warmup skeleton with two fills.

Three phases in a fixed order - designate anchors, gather evidence, seed the backlog -
filled differently depending on whether the account arrived with a Letterboxd export or
with nothing (onboarding-and-import.md). The skeleton is shared so that neither path is
a special case of the other, and so that an owner who imports later meets the same
three questions they would have met on day one.

Two rules run through everything here.

*The warmup is never a gate.* Every prompt is individually skippable, skipping any part
leaves the app fully usable, and nothing downstream asks whether the warmup finished.
What the phases report is therefore progress, not permission: a "done" here means the
question has stopped being worth asking, never that something was unlocked by it.

*Nothing is stored that could be derived.* Which bands have anchors, how much evidence
the log holds, what is in the backlog - all of it is read fresh on every request, so
the warmup cannot drift out of step with the account it is describing. The one thing
with no other trace is a skip, which records no judgment by definition, and that is the
whole content of :class:`~anchor.models.WarmupProgress`.

Designation stays the owner's act in both fills. This module ranks candidates and picks
questions; it never designates, and :mod:`anchor.anchors` is the only writer of an
anchor there is (ADR 0002).
"""

import enum
import random
import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from anchor import anchors as anchors_module
from anchor import bands, jobs
from anchor import ordering as ordering_module
from anchor import placement as placement_module
from anchor import readiness as readiness_module
from anchor.accounts import CurrentAccount
from anchor.catalog import FilmCard
from anchor.deps import AppJobs, AppSettings, DbSession
from anchor.errors import ApiError
from anchor.models import (
    AccountFilm,
    ComparisonContext,
    ComparisonKind,
    ComparisonLogEntry,
    ComparisonStatus,
    ComparisonVerdict,
    Film,
    Import,
    ImportRow,
    ImportRowKind,
    ImportRowState,
    LifecycleState,
    Placement,
    WarmupMark,
    WarmupProgress,
    WatchEvent,
)
from anchor.ordering import Ordering
from anchor.readiness import Readiness
from anchor.settings import Settings

router = APIRouter(prefix="/api/warmup")

WHOLE_STARS: tuple[float, ...] = (5.0, 1.0, 3.0, 4.0, 2.0)
"""The five whole-star prompts, in the spec's ease-of-recall order.

Best first because a favourite film is the one judgment nobody has to think about, then
worst, then the middle, then the two that are only findable once their neighbours exist.
"""

HALF_STARS: tuple[float, ...] = (4.5, 0.5, 2.5, 3.5, 1.5)
"""The optional continuation, in the same shape: outsides first, then the interior.

Offered rather than prompted, and always after the whole stars, because "a definitive
3.5" is a harder judgment than "a definitive 3" (onboarding-and-import.md).
"""


# --- Wire shapes ---


class Fill(enum.StrEnum):
    """Which fill the shared skeleton is running. Derived from the account, never chosen.

    The entry fork picks a *route*, not a fill: an owner who starts fresh and imports a
    month later gets the import fill from that moment, because the fill is only ever a
    reading of what the account actually holds.
    """

    imported = "imported"
    fresh = "fresh"


class PromptState(enum.StrEnum):
    """Where one prompt stands. Skipped and done are both terminal, and both fine."""

    todo = "todo"
    done = "done"
    skipped = "skipped"


class AnchorPrompt(BaseModel):
    """One band's designation prompt, and the films offered towards answering it."""

    band: float
    state: PromptState
    film: FilmCard | None
    """The anchor, once the owner has designated one. The prompt is done when it exists."""
    candidates: list[FilmCard]
    """Ranked suggestions for this band, on the import fill. Empty on the fresh fill,
    where the owner searches instead: a suggestion drawn from an empty library would be
    a popularity grid wearing the costume of a recommendation."""


class AnchorPhase(BaseModel):
    """Phase 1: erect the band structure by naming what each band means."""

    state: PromptState
    prompts: list[AnchorPrompt]
    """The five whole stars, in ease-of-recall order."""
    continuation: list[AnchorPrompt]
    """The five half stars, offered after the whole stars and never before them."""
    browse: bool
    """Offer the popular/top-rated grid as the explicit "need inspiration?" fallback."""


class EvidencePhase(BaseModel):
    """Phase 2: give the ordering something to work with, whichever way the fill asks."""

    state: PromptState
    kind: Literal["comparisons", "placements"]
    """Comparisons on the import fill, where a library already exists to compare within;
    placements on the fresh fill, where the films have to arrive before they can be."""
    answered: int
    target: int
    """Advisory: what the phase stops asking after, never what the owner has to reach."""


class BacklogPhase(BaseModel):
    """Phase 3: something to watch next, which is the one feature usable from minute one."""

    state: PromptState
    films: int
    seeded: int
    """Backlog films the import's watchlist.csv put there, which is the whole of the
    import fill's phase 3: it has already happened by the time the owner sees it."""


class Warmup(BaseModel):
    """The whole warmup, read fresh. Everything but the skips is derived."""

    fill: Fill
    fork: bool
    """Show the entry fork: the owner has not answered it either way yet."""
    dismissed: bool
    anchors: AnchorPhase
    evidence: EvidencePhase
    backlog: BacklogPhase
    readiness: Readiness
    """Ambient only, for the progress line surfacing.md allows on a warmup step."""


class Skip(BaseModel):
    """Skipping one prompt, or a whole phase where no band is named."""

    mark: Literal[WarmupMark.anchors, WarmupMark.evidence, WarmupMark.backlog]
    band: float | None = None


class Comparison(BaseModel):
    """One warmup comparison: two films from the same provisional tie-group.

    Carries no rating-shaped key at all, exactly as a placement question does not: the
    owner answers on the pure which-is-better instinct, and the seeds' shared band is
    the one thing that would give the answer away.
    """

    done: Literal[False] = False
    a: FilmCard
    b: FilmCard
    answered: int
    target: int
    unlocked: Readiness | None = None
    """The readiness this very answer crossed into, for the one line surfacing.md allows.
    Null on a plain read, and null on an answer that crossed nothing. It rides the next
    step rather than a response of its own because the unlock is worth exactly one line
    on the step the owner is already looking at, and nothing more (ADR 0011)."""


class EvidenceDone(BaseModel):
    """No question left worth asking: the target is met, or the seeds are all split."""

    done: Literal[True] = True
    answered: int
    target: int
    unlocked: Readiness | None = None
    """The readiness this very answer crossed into. See :class:`Comparison`."""


class ComparisonAnswer(BaseModel):
    a_tmdb_id: int
    b_tmdb_id: int
    verdict: ComparisonVerdict
    seed: int | None = None


EvidenceStep = Comparison | EvidenceDone


# --- Reading the warmup ---


@router.get("")
async def read(account: CurrentAccount, db: DbSession, settings: AppSettings) -> Warmup:
    return await _warmup(db, account.id, settings)


@router.post("/enter")
async def enter(account: CurrentAccount, db: DbSession, settings: AppSettings) -> Warmup:
    """The entry fork has been answered, whichever way it was answered.

    Both branches land here, because the fork's question is "which way in?" and either
    answer settles it. Which fill runs is read off the account rather than recorded, so
    an owner who picks "start fresh" and imports later is not held to the choice.
    """
    await _mark(db, account.id, WarmupMark.entered)
    await db.commit()
    return await _warmup(db, account.id, settings)


@router.post("/skip")
async def skip(body: Skip, account: CurrentAccount, db: DbSession, settings: AppSettings) -> Warmup:
    """Put one prompt, or one whole phase, away without answering it.

    A skip records no judgment - that is what makes it a skip - so the only thing it
    writes is that this prompt has been asked and should not be asked again.
    """
    if body.band is not None:
        if body.mark is not WarmupMark.anchors:
            raise ApiError(422, "not_a_band_prompt", "Only a designation prompt names a band.")
        if body.band not in bands.BANDS:
            raise ApiError(422, "not_a_band", "Ratings run from 0.5 to 5.0 in half-stars.")
    await _mark(db, account.id, body.mark, body.band)
    await db.commit()
    return await _warmup(db, account.id, settings)


@router.post("/dismiss")
async def dismiss(account: CurrentAccount, db: DbSession, settings: AppSettings) -> Warmup:
    """Put the whole warmup away. The app was fully usable before this and after it."""
    await _mark(db, account.id, WarmupMark.dismissed)
    await db.commit()
    return await _warmup(db, account.id, settings)


async def _warmup(db: AsyncSession, account_id: uuid.UUID, settings: Settings) -> Warmup:
    marks = await _marks(db, account_id)
    fill = await _fill(db, account_id)
    ordering = await ordering_module.load(db, account_id)
    boundaries = await bands.load(db, account_id)
    designations = await anchors_module.current(db, account_id)

    anchors = await _anchor_phase(db, account_id, settings, fill, marks, ordering, boundaries)
    evidence = await _evidence_phase(db, account_id, settings, fill, marks, len(designations))
    backlog = await _backlog_phase(db, account_id, marks)
    return Warmup(
        fill=fill,
        fork=(WarmupMark.entered, None) not in marks and (WarmupMark.dismissed, None) not in marks,
        dismissed=(WarmupMark.dismissed, None) in marks,
        anchors=anchors,
        evidence=evidence,
        backlog=backlog,
        readiness=readiness_module.classify(
            await readiness_module.evidence(db, account_id), settings
        ),
    )


async def _fill(db: AsyncSession, account_id: uuid.UUID) -> Fill:
    """Which fill this account is running: it has an import, or it has not."""
    imported = await db.scalar(select(Import.id).where(Import.account_id == account_id))
    return Fill.imported if imported is not None else Fill.fresh


# --- Phase 1: designate anchors ---


async def _anchor_phase(
    db: AsyncSession,
    account_id: uuid.UUID,
    settings: Settings,
    fill: Fill,
    marks: set[tuple[WarmupMark, float | None]],
    ordering: Ordering,
    boundaries: dict[float, int],
) -> AnchorPhase:
    designations = await anchors_module.current(db, account_id)
    ranked = (
        await _candidates(db, account_id, settings, ordering, boundaries, designations)
        if fill is Fill.imported
        else {}
    )
    cards = await ordering_module.cards(
        db, [*designations.values(), *(film for band in ranked.values() for film in band)]
    )

    def prompt(band: float) -> AnchorPrompt:
        designated = designations.get(band)
        if designated is not None:
            state = PromptState.done
        elif (WarmupMark.anchors, band) in marks or (WarmupMark.anchors, None) in marks:
            state = PromptState.skipped
        else:
            state = PromptState.todo
        return AnchorPrompt(
            band=band,
            state=state,
            film=cards.get(designated) if designated is not None else None,
            candidates=(
                [cards[film_id] for film_id in ranked.get(band, ()) if film_id in cards]
                if state is PromptState.todo
                else []
            ),
        )

    prompts = [prompt(band) for band in WHOLE_STARS]
    return AnchorPhase(
        state=_phase_state(
            skipped=(WarmupMark.anchors, None) in marks,
            done=all(one.state is not PromptState.todo for one in prompts),
        ),
        prompts=prompts,
        continuation=[prompt(band) for band in HALF_STARS],
        # Search is the headline act on the fresh fill and the grid is its stated
        # fallback; on the import fill the owner's own library is the better grid.
        browse=fill is Fill.fresh,
    )


async def _candidates(
    db: AsyncSession,
    account_id: uuid.UUID,
    settings: Settings,
    ordering: Ordering,
    boundaries: dict[float, int],
    designations: dict[float, int],
) -> dict[float, list[int]]:
    """The films worth offering per band, best-remembered first.

    The ranking is the spec's, and every term of it is a proxy for one question: which
    of these does the owner remember clearly enough to say "this is what a 4.0 is"? A
    film they went back to is remembered; a film they rated recently is remembered; a
    film half the world has seen is at least recognisable. Profile favourites jump their
    band outright, because the owner has already named them as the ones that matter.

    Popularity is read off the stored vote count rather than TMDB's own popularity
    figure, which is a churning daily metric Anchor does not keep: a candidate list that
    reshuffled overnight for reasons inside TMDB would be worse than a stable one.
    """
    derived = ordering_module.bands_of(ordering, boundaries)
    banded: dict[float, list[int]] = {}
    for film_id, band in derived.items():
        if band is not None and designations.get(band) != film_id:
            banded.setdefault(band, []).append(film_id)
    if not banded:
        return {}

    favorites = await _profile_favorites(db, account_id)
    rewatches = await _rewatch_counts(db, account_id)
    rated_at = await _rating_recency(db, account_id)
    popularity = await _vote_counts(db, [film for films in banded.values() for film in films])
    epoch = min(rated_at.values(), default=None)

    def key(film_id: int) -> tuple[object, ...]:
        when = rated_at.get(film_id)
        return (
            film_id not in favorites,
            -rewatches.get(film_id, 0),
            -(when.timestamp() if when is not None else (epoch.timestamp() - 1 if epoch else 0.0)),
            -popularity.get(film_id, 0),
            film_id,
        )

    return {
        band: sorted(films, key=key)[: settings.warmup_candidates_per_band]
        for band, films in banded.items()
    }


async def _profile_favorites(db: AsyncSession, account_id: uuid.UUID) -> set[int]:
    """The films profile.csv named as favourites, as far as they bound to anything."""
    rows = await db.scalars(
        select(ImportRow.film_id).where(
            ImportRow.account_id == account_id,
            ImportRow.kind == ImportRowKind.profile_favorite,
            ImportRow.state.in_((ImportRowState.auto_matched, ImportRowState.bound)),
            ImportRow.film_id.is_not(None),
        )
    )
    return {film_id for film_id in rows if film_id is not None}


async def _rewatch_counts(db: AsyncSession, account_id: uuid.UUID) -> dict[int, int]:
    """How many times the owner went back to each film, imported diary rows included."""
    rows = await db.execute(
        select(WatchEvent.film_id, func.count())
        .where(WatchEvent.account_id == account_id, WatchEvent.rewatch.is_(True))
        .group_by(WatchEvent.film_id)
    )
    return {film_id: count for film_id, count in rows}


async def _rating_recency(db: AsyncSession, account_id: uuid.UUID) -> dict[int, datetime]:
    """When each imported rating was given, which is the freshness of the memory behind it."""
    rows = await db.execute(
        select(ImportRow.film_id, func.max(ImportRow.occurred_at))
        .where(
            ImportRow.account_id == account_id,
            ImportRow.kind == ImportRowKind.rating,
            ImportRow.film_id.is_not(None),
            ImportRow.occurred_at.is_not(None),
        )
        .group_by(ImportRow.film_id)
    )
    return {film_id: when for film_id, when in rows}


async def _vote_counts(db: AsyncSession, film_ids: list[int]) -> dict[int, int]:
    if not film_ids:
        return {}
    rows = await db.execute(select(Film.tmdb_id, Film.vote_count).where(Film.tmdb_id.in_(film_ids)))
    return {tmdb_id: votes for tmdb_id, votes in rows}


# --- Phase 2: gather evidence ---


async def _evidence_phase(
    db: AsyncSession,
    account_id: uuid.UUID,
    settings: Settings,
    fill: Fill,
    marks: set[tuple[WarmupMark, float | None]],
    designated: int,
) -> EvidencePhase:
    if fill is Fill.imported:
        answered = await _warmup_answers(db, account_id)
        target = settings.warmup_comparisons
        kind: Literal["comparisons", "placements"] = "comparisons"
    else:
        # "Log ~5 films you have seen", counted as the placements that were not the
        # designations: designating already placed those, and asking the owner to do it
        # five more times is the point of the phase.
        placed = await db.scalar(
            select(func.count())
            .select_from(AccountFilm)
            .where(AccountFilm.account_id == account_id, AccountFilm.state == LifecycleState.rated)
        )
        answered = max(0, (placed or 0) - designated)
        target = settings.warmup_placements
        kind = "placements"
    return EvidencePhase(
        state=_phase_state(skipped=(WarmupMark.evidence, None) in marks, done=answered >= target),
        kind=kind,
        answered=answered,
        target=target,
    )


async def _warmup_answers(db: AsyncSession, account_id: uuid.UUID) -> int:
    """Warmup comparisons the owner actually answered. A skip is not an answer."""
    return (
        await db.scalar(
            select(func.count())
            .select_from(ComparisonLogEntry)
            .where(
                ComparisonLogEntry.account_id == account_id,
                ComparisonLogEntry.context == ComparisonContext.warmup,
                ComparisonLogEntry.verdict.is_not(None),
                ComparisonLogEntry.verdict != ComparisonVerdict.skip,
            )
        )
    ) or 0


@router.get("/comparison")
async def next_comparison(
    account: CurrentAccount,
    db: DbSession,
    settings: AppSettings,
    seed: Annotated[int | None, Query()] = None,
) -> EvidenceStep:
    """The next warmup comparison, or the news that there is none left worth asking."""
    return await _comparison_step(db, account.id, settings, seed)


@router.post("/comparison")
async def answer_comparison(
    body: ComparisonAnswer,
    account: CurrentAccount,
    db: DbSession,
    queue: AppJobs,
    settings: AppSettings,
) -> EvidenceStep:
    """Record one warmup comparison and split the pair out of its provisional group.

    The pair always comes out of one seeded tie-group, which is where a freshly imported
    ordering knows least: the import maps every Letterboxd value onto a band and
    fabricates no order inside it, so within-band order is the whole of what is missing.
    The answer is the two films' first real evidence, and it pulls them both out of the
    placeholder they were sitting in (onboarding-and-import.md).

    What the split claims is exactly what was answered. Both films stay provisional
    until their own judgments pin them, so their new positions against the rest of the
    group assert nothing the owner did not say - a provisional position is a placeholder
    rather than a judgment, which is what lets the group be taken apart at all.
    """
    before = readiness_module.classify(await readiness_module.evidence(db, account.id), settings)
    ordering = await ordering_module.load(db, account.id)
    index = await _shared_seeded_slot(db, account.id, ordering, body.a_tmdb_id, body.b_tmdb_id)

    db.add(
        ComparisonLogEntry(
            account_id=account.id,
            kind=ComparisonKind.overall,
            subject_film_id=body.a_tmdb_id,
            film_a_id=body.a_tmdb_id,
            film_b_id=body.b_tmdb_id,
            verdict=body.verdict,
            context=ComparisonContext.warmup,
            status=ComparisonStatus.active,
        )
    )
    await db.flush()

    if body.verdict is not ComparisonVerdict.skip:
        await _split(db, account.id, ordering, index, body)
        await placement_module.graduate(db, account.id, [body.a_tmdb_id, body.b_tmdb_id])
        await jobs.schedule_retrain(db, queue, account.id)
    await db.commit()

    step = await _comparison_step(db, account.id, settings, body.seed)
    after = readiness_module.classify(await readiness_module.evidence(db, account.id), settings)
    step.unlocked = after if after is not before else None
    return step


async def _split(
    db: AsyncSession,
    account_id: uuid.UUID,
    ordering: Ordering,
    index: int,
    body: ComparisonAnswer,
) -> None:
    """Take the answered pair out of the tie-group the import seeded them into.

    The winner leaves first, into a slot of its own at the group's position; the loser
    then leaves into the slot immediately under it, so the pair ends up adjacent and in
    the answered order with the rest of the group untouched below them. A Tied answer
    puts them in one slot together instead - a tie the owner actually made, which is a
    different thing from the seeded equality they were sitting in.

    Every read is taken again between the two moves, because opening a slot renumbers
    everything under it and an index from before the first move means nothing after it.
    """
    tied = body.verdict is ComparisonVerdict.tied
    winner, loser = (
        (body.a_tmdb_id, body.b_tmdb_id)
        if body.verdict is not ComparisonVerdict.b
        else (body.b_tmdb_id, body.a_tmdb_id)
    )
    first = await _placement_of(db, account_id, winner)
    await ordering_module.reseat(db, account_id, first, ordering, winner, index=index)

    await db.flush()
    ordering = await ordering_module.load(db, account_id)
    landed = ordering.index_of(winner)
    seat = ordering.index_of(loser)
    assert landed is not None and seat is not None  # both were just read out of one slot
    second = await _placement_of(db, account_id, loser)
    if tied:
        await ordering_module.reseat(
            db,
            account_id,
            second,
            ordering,
            loser,
            slot=await ordering_module.slot_by_id(db, ordering.slots[landed].id),
        )
    elif len(ordering.slots[seat].film_ids) > 1:
        # Still grouped, so it has to come out: inserting at its own slot's index puts
        # it above what it leaves behind and below the winner, which is what was said.
        await ordering_module.reseat(db, account_id, second, ordering, loser, index=seat)
    await db.flush()


async def _comparison_step(
    db: AsyncSession, account_id: uuid.UUID, settings: Settings, seed: int | None
) -> EvidenceStep:
    answered = await _warmup_answers(db, account_id)
    target = settings.warmup_comparisons
    pair = (
        await _choose_pair(db, account_id, seed)
        if answered < target and await _fill(db, account_id) is Fill.imported
        else None
    )
    if pair is None:
        return EvidenceDone(answered=answered, target=target)
    cards = await ordering_module.cards(db, list(pair))
    return Comparison(a=cards[pair[0]], b=cards[pair[1]], answered=answered, target=target)


async def _choose_pair(
    db: AsyncSession, account_id: uuid.UUID, seed: int | None
) -> tuple[int, int] | None:
    """The advisory pick: the two films this ordering learns most from being told about.

    The largest provisional tie-group is where the ordering knows least - an imported
    band of forty films is forty films in no order at all - so that is where the question
    goes, and a pair the owner has already been asked about never comes back. Which pair
    within the group is a coin flip the seed settles, and nothing is asserted by it: the
    group's members are seeded equal, so no member is a better question than any other.
    """
    ordering = await ordering_module.load(db, account_id)
    seeded = await ordering_module.seeded_slot_ids(db, account_id)
    asked = await _asked_pairs(db, account_id)
    rng = random.Random(seed if seed is not None else _seed(account_id))
    groups = sorted(
        (slot for slot in ordering.slots if slot.id in seeded and len(slot.film_ids) > 1),
        key=lambda slot: (-len(slot.film_ids), slot.position),
    )
    for slot in groups:
        members = sorted(slot.film_ids)
        pairs = [
            (a, b)
            for i, a in enumerate(members)
            for b in members[i + 1 :]
            if frozenset((a, b)) not in asked
        ]
        if pairs:
            return rng.choice(pairs)
    return None


async def _asked_pairs(db: AsyncSession, account_id: uuid.UUID) -> set[frozenset[int]]:
    """Every pair the owner has already judged, however they judged it.

    Skips count: declining to compare two films is an answer about the question, and
    putting it back in front of them would be asking them to decline it again.
    """
    rows = await db.execute(
        select(ComparisonLogEntry.film_a_id, ComparisonLogEntry.film_b_id).where(
            ComparisonLogEntry.account_id == account_id,
            ComparisonLogEntry.film_b_id.is_not(None),
        )
    )
    return {frozenset((a, b)) for a, b in rows if a is not None and b is not None}


async def _shared_seeded_slot(
    db: AsyncSession, account_id: uuid.UUID, ordering: Ordering, a: int, b: int
) -> int:
    """The provisional tie-group both films sit in, refusing anything else.

    A stale answer - given from a screen left open while the ordering moved on - would
    otherwise write a judgment against a group that no longer exists, and the log is
    append-only, so there would be no taking it back.
    """
    if a == b:
        raise ApiError(422, "same_film", "A film cannot be compared with itself.")
    index = ordering.index_of(a)
    if index is None or ordering.index_of(b) != index:
        raise ApiError(409, "stale_question", "That comparison is no longer the one being asked.")
    if ordering.slots[index].id not in await ordering_module.seeded_slot_ids(db, account_id):
        raise ApiError(409, "stale_question", "That comparison is no longer the one being asked.")
    return index


async def _placement_of(db: AsyncSession, account_id: uuid.UUID, film_id: int) -> Placement:
    placement = await db.scalar(
        select(Placement)
        .join(AccountFilm, AccountFilm.id == Placement.account_film_id)
        .where(Placement.account_id == account_id, AccountFilm.film_id == film_id)
    )
    assert placement is not None  # a film in the ordering is a placed film
    return placement


# --- Phase 3: seed the backlog ---


async def _backlog_phase(
    db: AsyncSession, account_id: uuid.UUID, marks: set[tuple[WarmupMark, float | None]]
) -> BacklogPhase:
    films = (
        await db.scalar(
            select(func.count())
            .select_from(AccountFilm)
            .where(
                AccountFilm.account_id == account_id,
                AccountFilm.state == LifecycleState.backlog,
            )
        )
    ) or 0
    seeded = (
        await db.scalar(
            select(func.count(func.distinct(ImportRow.film_id))).where(
                ImportRow.account_id == account_id,
                ImportRow.kind == ImportRowKind.watchlist,
                ImportRow.state.in_((ImportRowState.auto_matched, ImportRowState.bound)),
            )
        )
    ) or 0
    return BacklogPhase(
        state=_phase_state(skipped=(WarmupMark.backlog, None) in marks, done=films > 0),
        films=films,
        seeded=seeded,
    )


# --- The marks ---


async def _marks(db: AsyncSession, account_id: uuid.UUID) -> set[tuple[WarmupMark, float | None]]:
    rows = await db.execute(
        select(WarmupProgress.mark, WarmupProgress.band).where(
            WarmupProgress.account_id == account_id
        )
    )
    return {(mark, band) for mark, band in rows}


async def _mark(
    db: AsyncSession, account_id: uuid.UUID, mark: WarmupMark, band: float | None = None
) -> None:
    """Record a mark once. Marking twice is something the owner is allowed to ask for."""
    if (mark, band) in await _marks(db, account_id):
        return
    db.add(WarmupProgress(account_id=account_id, mark=mark, band=band))


def _phase_state(*, skipped: bool, done: bool) -> PromptState:
    """Skipped wins over done: an owner who put a phase away is not owed a tick for it."""
    if skipped:
        return PromptState.skipped
    return PromptState.done if done else PromptState.todo


def _seed(account_id: uuid.UUID) -> int:
    """Stable per account, so asking twice without answering offers the same pair."""
    return account_id.int % (2**32)
