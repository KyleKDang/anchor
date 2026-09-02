"""The placement flow: finding a film's slot in the ordering, and then its band.

One question at a time, four answers - A, B, Tied, Skip - and the search bisects the
ordering between them. Four things make this flow what it is:

*The search has no state of its own.* An in-progress placement is exactly its answers,
which are already in the append-only comparison log, so every step re-derives the search
bounds from that log. Abandoning is therefore free (nothing to clean up) and resuming is
automatic: a later attempt reads the same answers and picks up where the owner left off.

*Opponent selection is advisory only* (ADR 0001). It picks which film to ask about and
nothing else; every bound the search holds came from an owner's answer. It samples, so
it takes a seed, and a scripted answer sequence lands deterministically (testing.md). A
ballpark guess steers that first pick and nothing else: it never becomes a judgment, and
it is never logged, because a hunch pinning dividers would quietly reintroduce the
drifting absolute scale the whole design exists to avoid.

*Nothing rating-shaped leaves here mid-comparison.* The owner answers on the pure
which-is-better instinct, uncontaminated by the opponent's band, so the values are absent
from the payload rather than hidden by the client. The band question is the deliberate
exception: it is *about* the bands, so it names them.

*Position decides the band, except in one place.* A landing that falls exactly on a
divider belongs to neither side until the owner says, and that is the sliver question -
asked before the slot is ever opened, because the answer is what decides which way the
divider moves. Where the two bands have no exemplars to compare against, it degrades to
a plain band pick; where the ambiguity is a hole in the scale rather than a boundary,
nothing is asked at all and the film honestly shows its position.

There is no undo, by design: the log is append-only and re-placement is the correction
path. Keep-comparing is the same flow extended around a landed position, and the doubt
alone never moves anything - only the answers do.
"""

import random
import uuid
import zlib
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from anchor import anchors as anchors_module
from anchor import bands
from anchor import ordering as ordering_module
from anchor.accounts import CurrentAccount
from anchor.bands import Boundaries
from anchor.catalog import FilmCard
from anchor.deps import DbSession
from anchor.errors import ApiError
from anchor.models import (
    Account,
    AccountFilm,
    ComparisonContext,
    ComparisonKind,
    ComparisonLogEntry,
    ComparisonStatus,
    ComparisonVerdict,
    LifecycleState,
    Placement,
    PlacementProvenance,
    PlacementTrust,
    TieGroupSlot,
)
from anchor.ordering import Ordering

router = APIRouter(prefix="/api/placements")


# --- The search, derived from the log ---


@dataclass(frozen=True)
class Search:
    """Where the owner's answers so far have narrowed the film's landing to.

    ``lo`` and ``hi`` are *insertion* indices, not slot indices, and they bound the
    landing inclusively: the film belongs at some index in ``[lo, hi]``, where 0 is
    above everything and ``len(ordering)`` is below everything. ``lo == hi`` means the
    answers have settled on one index and the search is over.

    The slots still worth asking about are therefore ``[lo, hi)`` - inserting at index
    ``i`` sits above the slot at ``i``, so the slot at ``hi`` is already known to be
    below the film and has nothing left to tell us.
    """

    lo: int
    hi: int
    tied_with: int | None
    """Set once the owner answers Tied, which ends the search definitively."""
    skipped: frozenset[int]
    """Films the owner declined to judge; they are never offered again this flow."""
    answered: int
    """Judgments recorded. Skips are not judgments and do not count."""

    @property
    def settled(self) -> bool:
        """The answers have narrowed the landing to exactly one index."""
        return self.lo == self.hi

    @property
    def midpoint(self) -> int:
        return (self.lo + self.hi) // 2


def derive(subject: int, ordering: Ordering, entries: list[ComparisonLogEntry]) -> Search:
    """Re-read the owner's answers into the bounds they imply, oldest first.

    Every constraint here is an owner judgment: "better than the film at slot 5" puts the
    landing at or above 5, "worse" puts it below. Opponents are read at their *current*
    slot index, so a placement that happened in between - which shifts indices but never
    reorders - leaves these bounds saying exactly what they said before.
    """
    lo, hi = 0, len(ordering)
    tied_with: int | None = None
    skipped: set[int] = set()
    answered = 0
    for entry in entries:
        opponent = entry.film_b_id if entry.film_a_id == subject else entry.film_a_id
        if opponent is None:
            continue  # a band judgment, which says nothing about position
        index = ordering.index_of(opponent)
        if index is None:
            continue  # the opponent is not rated, so it says nothing about the ordering
        if entry.verdict is ComparisonVerdict.skip:
            skipped.add(opponent)
            continue
        answered += 1
        if entry.verdict is ComparisonVerdict.tied:
            return Search(lo, hi, opponent, frozenset(skipped), answered)
        subject_won = (entry.verdict is ComparisonVerdict.a) == (entry.film_a_id == subject)
        if subject_won:
            hi = min(hi, index)
        else:
            lo = max(lo, index + 1)
    return Search(lo, hi, tied_with, frozenset(skipped), answered)


def locked_band(boundaries: Boundaries, search: Search) -> float | None:
    """The band every landing still in range would give, once the search is inside one.

    The spec's "walks bands until the band locks, then bisects within the band" read off
    the bounds rather than driven as a separate phase: the same bisection narrows both,
    and the band is locked the moment no remaining index could change it. That is what
    makes an early bail safe to offer - the stars are already settled, and only the
    exact neighbours are still open.
    """
    reachable = {
        bands.bands_possible_for_insertion(boundaries, index)
        for index in range(search.lo, search.hi + 1)
    }
    if len(reachable) != 1:
        return None
    only = next(iter(reachable))
    return only[0] if len(only) == 1 else None


def _as_reduced(ordering: Ordering, boundaries: Boundaries, film_id: int) -> Boundaries:
    """The dividers as they read against the ordering with this film lifted out of it.

    A re-placement searches a sequence its own film is not in, so the indices it works
    in are one short wherever the film sat above a divider. Lifting the film out of the
    dividers too keeps both halves of the answer talking about the same sequence. This
    is a read: the stored positions move only when the film actually does.
    """
    index = ordering.index_of(film_id)
    if index is None or len(ordering.slots[index].film_ids) > 1:
        return boundaries
    return bands.after_remove(boundaries, index)


def choose_opponent(
    ordering: Ordering, search: Search, seed: int, prefer: int | None = None
) -> int | None:
    """The advisory pick: which film to ask about next, or None when none is left.

    Bisection wants the midpoint of the live range, so candidates are ranked by distance
    from it and the seeded generator only breaks ties - between the slot above and the
    slot below, and between the members of one tie-group. A skipped film drops out, which
    is how Skip swaps in another opponent. ``prefer`` is the ballpark guess's whole
    effect: it jumps the queue for one question and constrains nothing.
    """
    rng = random.Random(seed)
    if prefer is not None and prefer not in search.skipped:
        index = ordering.index_of(prefer)
        if index is not None and search.lo <= index < search.hi:
            return prefer
    ranked = sorted(
        range(search.lo, search.hi), key=lambda index: (abs(index - search.midpoint), rng.random())
    )
    for index in ranked:
        members = [
            film_id for film_id in ordering.slots[index].film_ids if film_id not in search.skipped
        ]
        if members:
            return rng.choice(members)
    return None


# --- Wire shapes ---


class PlacementQuestion(BaseModel):
    """One comparison step: two films, and nothing that could bias the answer."""

    done: Literal[False] = False
    kind: Literal["comparison"] = "comparison"
    a: FilmCard
    """Always the film being placed."""
    b: FilmCard
    """The opponent the advisory math chose."""
    answered: int
    band_locked: bool
    """The stars are settled and only the neighbours are open, so bailing out is safe."""


class BandOption(BaseModel):
    """One band the landing could belong to, and the film that stands for it."""

    band: float
    exemplar: FilmCard | None
    """The band's anchor, or its most confidently-placed stand-in; None if it holds none."""


class BandQuestion(BaseModel):
    """The landing sits exactly on a divider, so only the owner can say which side.

    ``sliver`` marks the question the spec names: two adjacent bands, both with a
    canonical film to compare against. Anything else is the plain band pick the fallback
    ladder ends on, which offers the bands themselves.
    """

    done: Literal[False] = False
    kind: Literal["band"] = "band"
    film: FilmCard
    sliver: bool
    options: list[BandOption]
    answered: int


class Neighbours(BaseModel):
    """A landed film's immediate surroundings in the ordering."""

    above: list[FilmCard]
    tied_with: list[FilmCard]
    below: list[FilmCard]


class PlacementLanded(BaseModel):
    """The done screen: where the film landed, what that makes it, and who it sits between."""

    done: Literal[True] = True
    kind: Literal["landed"] = "landed"
    film: FilmCard
    position: int
    """1-based rank of the film's slot, best first."""
    total: int
    rating: float | None
    """Derived from position against the dividers; None while the band structure is thin."""
    band_anchor: bool
    """This film is its band's canonical exemplar."""
    provisional: bool
    """The position is trusted less than a fully-compared one, and settles on its own."""
    anchor_nudge: bool
    """Position-only and no anchor exists yet: the one line that explains the missing stars."""
    designated: bool
    """A designation-mismatch re-placement landed in its band and completed the intent."""
    neighbours: Neighbours


PlacementStep = PlacementQuestion | BandQuestion | PlacementLanded

Seed = Annotated[int | None, Query(ge=0)]
"""Overrides the advisory seed, so a scripted answer sequence lands deterministically."""

Ballpark = Annotated[float | None, Query(ge=0.5, le=5.0)]
"""An optional half-star hunch. Seeds the first question and never becomes a judgment."""


class Answer(BaseModel):
    """One judgment. ``a`` means the film being placed won; ``b`` means the opponent did."""

    opponent_tmdb_id: int
    verdict: ComparisonVerdict
    seed: int | None = None


class BandAnswer(BaseModel):
    """One band judgment: which of the offered bands the film belongs in."""

    band: float
    exemplar_tmdb_id: int | None = None
    """The canonical film the owner judged it closest to, where the question offered one."""
    seed: int | None = None


# --- What flow is running ---


@dataclass(frozen=True)
class Flow:
    """Which of the three flows this film is in, and which answers count towards it.

    All three are the same search over the same log; they differ only in the context
    their answers are written under and, for a re-placement, in the moment the flow
    started - the owner's intent outlives the request that made it, and the answers
    given before it belong to the placement that already landed.
    """

    account_film: AccountFilm
    context: ComparisonContext
    since: datetime | None

    @property
    def film_id(self) -> int:
        return self.account_film.film_id


async def _flow(db: AsyncSession, account: Account, account_film: AccountFilm) -> Flow:
    if account_film.state is not LifecycleState.rated:
        return Flow(account_film, ComparisonContext.placement, None)
    held = await anchors_module.intent(db, account.id)
    if held is not None and held.account_film_id == account_film.id:
        return Flow(account_film, ComparisonContext.re_placement, held.designated_at)
    return Flow(account_film, ComparisonContext.keep_comparing, None)


# --- The flow ---


@router.post("/{tmdb_id}")
async def begin(
    tmdb_id: int,
    account: CurrentAccount,
    db: DbSession,
    seed: Seed = None,
    ballpark: Ballpark = None,
    ballpark_to: Ballpark = None,
) -> PlacementStep:
    """Start or resume placing a film; a landed one just shows where it sits.

    Safe to call again at any time, and the placement screen does exactly that on every
    mount: with no state of its own to rebuild, resuming is just re-reading the log, and
    a film that has already landed simply shows where it landed. A designation mismatch
    is the exception - an intent is waiting, so this resumes the re-placement it started.
    """
    account_film = await _placeable(db, account, tmdb_id)
    flow = await _flow(db, account, account_film)
    if flow.context is ComparisonContext.keep_comparing:
        return await _landed(db, account, account_film)
    return await _advance(db, account, flow, seed, (ballpark, ballpark_to))


@router.post("/{tmdb_id}/answers")
async def answer(
    tmdb_id: int, body: Answer, account: CurrentAccount, db: DbSession
) -> PlacementStep:
    """Record one comparison and ask the next question, or land the film."""
    account_film = await _placeable(db, account, tmdb_id)
    flow = await _flow(db, account, account_film)
    ordering = await ordering_module.load(db, account.id)
    reduced = ordering.without(tmdb_id)
    if flow.context is ComparisonContext.keep_comparing:
        return await _extend(db, account, flow, ordering, reduced, body)

    search = derive(tmdb_id, reduced, await _entries(db, account.id, flow))
    _check_answerable(reduced, search, body.opponent_tmdb_id)
    db.add(_comparison(account.id, flow, tmdb_id, body.opponent_tmdb_id, body.verdict))
    await db.flush()
    return await _advance(db, account, flow, body.seed, (None, None))


@router.post("/{tmdb_id}/band")
async def band_answer(
    tmdb_id: int, body: BandAnswer, account: CurrentAccount, db: DbSession
) -> PlacementStep:
    """Record the band judgment that settles a landing sitting exactly on a divider."""
    account_film = await _placeable(db, account, tmdb_id)
    flow = await _flow(db, account, account_film)
    ordering = await ordering_module.load(db, account.id)
    reduced = ordering.without(tmdb_id)
    boundaries = await bands.load(db, account.id)

    if flow.context is ComparisonContext.keep_comparing:
        index = ordering.index_of(tmdb_id)
        assert index is not None  # a keep-comparing film is a landed film
        _check_offered(
            tuple(band for edge in _edges(boundaries, index) for band in edge), body.band
        )
        judgment = bands.judgment(
            account.id,
            film_id=tmdb_id,
            band=body.band,
            context=flow.context,
            exemplar=body.exemplar_tmdb_id,
        )
        db.add(judgment)
        await db.flush()
        moved = await bands.move(
            db, account.id, boundaries, bands.pins_for(boundaries, index, body.band), judgment
        )
        # The divider crossed the whole slot, so an anchor tied inside it changed band
        # too - and a canonical 4.0 among the 3.5s is a contradiction in terms.
        await anchors_module.retire_strays(db, account.id, ordering, moved)
        await db.commit()
        return await _landed(db, account, account_film)

    search = derive(tmdb_id, reduced, await _entries(db, account.id, flow))
    lifted = _as_reduced(ordering, boundaries, tmdb_id)
    _check_offered(bands.undecided_at(lifted, _landing(search).index), body.band)
    db.add(
        bands.judgment(
            account.id,
            film_id=tmdb_id,
            band=body.band,
            context=flow.context,
            exemplar=body.exemplar_tmdb_id,
        )
    )
    await db.flush()
    return await _advance(db, account, flow, body.seed, (None, None))


@router.post("/{tmdb_id}/bail")
async def bail(tmdb_id: int, account: CurrentAccount, db: DbSession) -> PlacementStep:
    """Stop here: the band is locked, so land provisionally and let the rest settle later.

    Offered only once the stars cannot change, because bailing before that would leave a
    film with no rating and no way to get one but starting over.
    """
    account_film = await _placeable(db, account, tmdb_id)
    flow = await _flow(db, account, account_film)
    if flow.context is ComparisonContext.keep_comparing:
        return await _landed(db, account, account_film)
    ordering = await ordering_module.load(db, account.id)
    reduced = ordering.without(tmdb_id)
    search = derive(tmdb_id, reduced, await _entries(db, account.id, flow))
    lifted = _as_reduced(ordering, await bands.load(db, account.id), tmdb_id)
    if locked_band(lifted, search) is None:
        raise ApiError(409, "band_not_locked", "Answer until the rating settles, then stop.")
    return await _advance(db, account, flow, None, (None, None), bailing=True)


@router.post("/{tmdb_id}/keep-comparing")
async def keep_comparing(
    tmdb_id: int, account: CurrentAccount, db: DbSession, seed: Seed = None
) -> PlacementStep:
    """Extend a landed placement with comparisons and band questions around its position.

    The doubt alone moves nothing: this only chooses what to ask. If every answer keeps
    the film where it is, the placement stands and the feeling was scale drift.
    """
    account_film = await _placeable(db, account, tmdb_id)
    if account_film.state is not LifecycleState.rated:
        raise ApiError(409, "not_placed", "Place this film before extending its placement.")
    return await _extension_question(db, account, account_film, seed)


# --- Landing ---


@dataclass(frozen=True)
class Landing:
    """Where the answers put the film, and how settled that is."""

    index: int
    """An insertion index against the ordering with this film lifted out of it."""
    tie_slot: TieGroupSlot | None
    provenance: PlacementProvenance


def _landing(search: Search, *, bailing: bool = False) -> Landing:
    if search.settled and not bailing:
        return Landing(search.lo, None, PlacementProvenance.completed)
    return Landing(search.midpoint, None, PlacementProvenance.early_bail)


async def _advance(
    db: AsyncSession,
    account: Account,
    flow: Flow,
    seed: int | None,
    ballpark: tuple[float | None, float | None],
    *,
    bailing: bool = False,
) -> PlacementStep:
    """Land the film if the answers have settled it, otherwise ask the next question."""
    tmdb_id = flow.film_id
    ordering = await ordering_module.load(db, account.id)
    reduced = ordering.without(tmdb_id)
    boundaries = _as_reduced(ordering, await bands.load(db, account.id), tmdb_id)
    entries = await _entries(db, account.id, flow)
    search = derive(tmdb_id, reduced, entries)

    if search.tied_with is not None:
        index = reduced.index_of(search.tied_with)
        assert index is not None  # derive only ties against a film it found in the ordering
        tie_slot = await ordering_module.slot_by_id(db, reduced.slots[index].id)
        landing = Landing(index, tie_slot, PlacementProvenance.completed)
    elif search.settled or bailing:
        landing = _landing(search, bailing=bailing)
    elif (
        opponent := choose_opponent(
            reduced,
            search,
            _seed(account.id, tmdb_id, seed),
            await _ballpark_opponent(db, account.id, ballpark),
        )
    ) is not None:
        await db.commit()
        return await _question(db, tmdb_id, opponent, search, boundaries)
    else:
        # Every film still in range has been skipped, so there is no question left to
        # ask. The owner's answers still hold - the film belongs somewhere in the
        # remaining range - but none of them picked the exact spot, so it lands
        # mid-range and is trusted less, and graduation comes back to it later rather
        # than treating a guessed position as a settled judgment.
        landing = _landing(search, bailing=True)

    judgment = await _band_judgment(db, account.id, flow)
    if landing.tie_slot is None and judgment is None:
        candidates = bands.undecided_at(boundaries, landing.index)
        if candidates:
            await db.commit()
            return await _band_step(
                db, account.id, tmdb_id, reduced, boundaries, candidates, search
            )

    await _seat(db, account, flow, ordering, landing, judgment)
    designated = await _settle(db, account, flow)
    # The opponents graduate here too, not just the film that was being placed: every
    # answer was a judgment about both films, and it becomes evidence about the opponent
    # the moment this film has a slot for it to be read against (onboarding-and-import.md).
    await _graduate(db, account.id, [tmdb_id, *_opponents(entries, tmdb_id)])
    await db.commit()
    return await _landed(db, account, flow.account_film, designated=designated)


async def _seat(
    db: AsyncSession,
    account: Account,
    flow: Flow,
    ordering: Ordering,
    landing: Landing,
    judgment: ComparisonLogEntry | None,
) -> None:
    """Put the film where the landing says, whether it is arriving or moving."""
    band = judgment.band if judgment is not None else None
    if flow.account_film.state is LifecycleState.rated:
        placement = await _placement(db, flow.account_film)
        await ordering_module.reseat(
            db,
            account.id,
            placement,
            ordering,
            flow.film_id,
            index=None if landing.tie_slot is not None else landing.index,
            slot=landing.tie_slot,
            band=band,
            judgment=judgment,
        )
        placement.trust = (
            PlacementTrust.full
            if landing.provenance is PlacementProvenance.completed
            else PlacementTrust.provisional
        )
        placement.provenance = landing.provenance
        return

    slot = landing.tie_slot or await ordering_module.new_slot(
        db, account.id, landing.index, band=band, judgment=judgment
    )
    ordering_module.land(db, flow.account_film, slot=slot, provenance=landing.provenance)


async def _settle(db: AsyncSession, account: Account, flow: Flow) -> bool:
    """Complete or cancel a designation intent, and retire an anchor carried out of band."""
    if flow.context is not ComparisonContext.re_placement:
        return False
    await db.flush()
    ordering = await ordering_module.load(db, account.id)
    boundaries = await bands.load(db, account.id)
    index = ordering.index_of(flow.film_id)
    assert index is not None  # the film was just seated, inside this request
    designated = await anchors_module.settle_intent(
        db, account.id, flow.account_film, index, boundaries
    )
    if not designated:
        await anchors_module.retire_if_outside(db, account.id, flow.account_film, boundaries, index)
    return designated


# --- Keep comparing ---


async def _extension_question(
    db: AsyncSession, account: Account, account_film: AccountFilm, seed: int | None
) -> PlacementStep:
    """The next question around a landed position, or the done screen when none is left.

    Band-edge questions come first: they are the ones that can change the stars, which
    is what the owner opened this for. Then the immediate neighbours, which can move the
    film past one. Everything already answered in this extension drops out, so the flow
    ends rather than looping.
    """
    tmdb_id = account_film.film_id
    ordering = await ordering_module.load(db, account.id)
    boundaries = await bands.load(db, account.id)
    index = ordering.index_of(tmdb_id)
    assert index is not None  # only a placed film reaches here
    asked = await _already_extended(db, account.id, tmdb_id)

    for edge in _edges(boundaries, index):
        if asked.bands.intersection(edge):
            continue
        offered = await anchors_module.exemplars(
            db, account.id, ordering, boundaries, edge, exclude=tmdb_id
        )
        if len(offered) == len(edge):
            return await _band_question(db, tmdb_id, edge, offered, sliver=True, answered=0)

    for neighbour in _neighbours(ordering, index):
        if neighbour not in asked.films:
            search = Search(index, index, None, frozenset(), 0)
            return await _question(db, tmdb_id, neighbour, search, boundaries, extending=True)
    return await _landed(db, account, account_film)


async def _extend(
    db: AsyncSession,
    account: Account,
    flow: Flow,
    ordering: Ordering,
    reduced: Ordering,
    body: Answer,
) -> PlacementStep:
    """Apply one keep-comparing comparison: it may move the film, and nothing else may.

    An answer that agrees with where the film already sits moves nothing at all - the
    placement simply stands, and what felt wrong was the scale drifting under it.
    """
    tmdb_id = flow.film_id
    index = ordering.index_of(tmdb_id)
    assert index is not None  # keep-comparing only runs on a landed film
    if body.opponent_tmdb_id not in _neighbours(ordering, index):
        raise ApiError(409, "stale_question", "That comparison is no longer the one being asked.")
    db.add(_comparison(account.id, flow, tmdb_id, body.opponent_tmdb_id, body.verdict))
    await db.flush()

    opponent = reduced.index_of(body.opponent_tmdb_id)
    assert opponent is not None  # a neighbour is a rated film other than this one
    placement = await _placement(db, flow.account_film)
    moved = False
    if body.verdict is ComparisonVerdict.tied:
        slot = await ordering_module.slot_by_id(db, reduced.slots[opponent].id)
        await ordering_module.reseat(db, account.id, placement, ordering, tmdb_id, slot=slot)
        moved = True
    elif _moves_the_film(ordering, index, body):
        await ordering_module.reseat(
            db,
            account.id,
            placement,
            ordering,
            tmdb_id,
            index=opponent if body.verdict is ComparisonVerdict.a else opponent + 1,
        )
        moved = True
    if moved:
        # The film crossed a divider under its own answer rather than a divider moving
        # under it, so an anchor carried across changed band without being re-placed.
        # The lifecycle's answer is re-placement's: the status goes, the position stays.
        await db.flush()
        await anchors_module.retire_strays(
            db,
            account.id,
            await ordering_module.load(db, account.id),
            await bands.load(db, account.id),
        )
    await _graduate(db, account.id, [tmdb_id, body.opponent_tmdb_id])
    await db.commit()
    return await _landed(db, account, flow.account_film)


def _moves_the_film(ordering: Ordering, index: int, body: Answer) -> bool:
    """Whether the answer contradicts the film's current position, which is the only mover."""
    above = index > 0 and body.opponent_tmdb_id in ordering.slots[index - 1].film_ids
    return body.verdict is (ComparisonVerdict.a if above else ComparisonVerdict.b)


def _neighbours(ordering: Ordering, index: int) -> list[int]:
    """The films in the slots either side: the only ones an extension asks about."""
    above = ordering.slots[index - 1].film_ids if index > 0 else ()
    below = ordering.slots[index + 1].film_ids if index + 1 < len(ordering) else ()
    return [*above, *below]


def _edges(boundaries: Boundaries, index: int) -> list[tuple[float, ...]]:
    """The band pairs a slot sits against: the divider just above it, and just below.

    Only a slot pressed right against a divider has a band question worth asking - with
    a film in between, the answer would be about that film's position, not this one's -
    and both sides are worth asking, because "is this really only a 3.5?" and "is this
    really a whole 4.0?" are different doubts with different answers.
    """
    return [
        (upper, bands.BANDS[bands.rank(upper) + 1])
        for upper, boundary in sorted(boundaries.items(), key=lambda pair: pair[1])
        if boundary in (index, index + 1)
    ]


@dataclass(frozen=True)
class Extended:
    """What this extension has already asked, so it never asks the same thing twice."""

    films: frozenset[int]
    bands: frozenset[float]


async def _already_extended(db: AsyncSession, account_id: uuid.UUID, tmdb_id: int) -> Extended:
    entries = await db.scalars(
        select(ComparisonLogEntry).where(
            ComparisonLogEntry.account_id == account_id,
            ComparisonLogEntry.subject_film_id == tmdb_id,
            ComparisonLogEntry.context == ComparisonContext.keep_comparing,
            ComparisonLogEntry.status == ComparisonStatus.active,
        )
    )
    films: set[int] = set()
    banded: set[float] = set()
    for entry in entries:
        if entry.band is not None:
            # The answer names one band, but it settled the pair it was offered as, so
            # the neighbour goes down with it and the same edge is not re-asked.
            banded.update({entry.band, *_neighbouring_bands(entry.band)})
        elif entry.film_b_id is not None:
            films.add(entry.film_b_id)
    return Extended(frozenset(films), frozenset(banded))


def _neighbouring_bands(band: float) -> tuple[float, ...]:
    position = bands.rank(band)
    return tuple(
        bands.BANDS[index]
        for index in (position - 1, position + 1)
        if 0 <= index < len(bands.BANDS)
    )


# --- Graduation ---


async def _graduate(db: AsyncSession, account_id: uuid.UUID, film_ids: list[int]) -> None:
    """Promote a provisional placement whose answers now pin it as tightly as a full one.

    One rule for both kinds of provisional, seed-imported and early-bailed: the position
    is fully trusted once re-deriving it from the film's own judgments lands on exactly
    the slot it already occupies - the same bar a normal placement clears the moment its
    search settles. The advisory math judges the confidence and nothing else: the slot
    does not move here, whatever the answers say (ADR 0001).
    """
    await db.flush()
    ordering = await ordering_module.load(db, account_id)
    for film_id in film_ids:
        placement = await db.scalar(
            select(Placement)
            .join(AccountFilm, AccountFilm.id == Placement.account_film_id)
            .where(
                Placement.account_id == account_id,
                AccountFilm.film_id == film_id,
                Placement.trust == PlacementTrust.provisional,
            )
        )
        index = ordering.index_of(film_id)
        if placement is None or index is None:
            continue
        search = derive(
            film_id, ordering.without(film_id), await _evidence(db, account_id, film_id)
        )
        if (
            search.tied_with in ordering.slots[index].film_ids
            or len(ordering.slots[index].film_ids) == 1
            and search.settled
            and search.lo == index
        ):
            placement.trust = PlacementTrust.full


# --- Wire assembly ---


async def _question(
    db: AsyncSession,
    tmdb_id: int,
    opponent: int,
    search: Search,
    boundaries: Boundaries,
    *,
    extending: bool = False,
) -> PlacementQuestion:
    cards = await ordering_module.cards(db, [tmdb_id, opponent])
    return PlacementQuestion(
        a=cards[tmdb_id],
        b=cards[opponent],
        answered=search.answered,
        band_locked=extending or locked_band(boundaries, search) is not None,
    )


async def _band_step(
    db: AsyncSession,
    account_id: uuid.UUID,
    tmdb_id: int,
    reduced: Ordering,
    boundaries: Boundaries,
    candidates: tuple[float, ...],
    search: Search,
) -> BandQuestion:
    """The fallback ladder, one rung at a time: sliver where it can be, plain pick where not.

    Two adjacent bands with a canonical film each is the sliver question the spec names.
    Anything else - three bands because one between them holds nothing, or a band with
    no exemplar to stand for it - still has to be answerable, so it degrades to the pick.
    """
    offered = await anchors_module.exemplars(db, account_id, reduced, boundaries, candidates)
    sliver = len(candidates) == 2 and len(offered) == 2
    return await _band_question(db, tmdb_id, candidates, offered, sliver, search.answered)


async def _band_question(
    db: AsyncSession,
    tmdb_id: int,
    candidates: tuple[float, ...],
    offered: dict[float, int],
    sliver: bool,
    answered: int,
) -> BandQuestion:
    cards = await ordering_module.cards(db, [tmdb_id, *offered.values()])
    return BandQuestion(
        film=cards[tmdb_id],
        sliver=sliver,
        options=[
            BandOption(band=band, exemplar=cards.get(offered[band]) if band in offered else None)
            for band in candidates
        ],
        answered=answered,
    )


async def _landed(
    db: AsyncSession,
    account: Account,
    account_film: AccountFilm,
    *,
    designated: bool = False,
) -> PlacementLanded:
    tmdb_id = account_film.film_id
    ordering = await ordering_module.load(db, account.id)
    boundaries = await bands.load(db, account.id)
    index = ordering.index_of(tmdb_id)
    assert index is not None  # the film was just placed, inside this request
    slot = ordering.slots[index]
    above = ordering.slots[index - 1].film_ids if index > 0 else ()
    below = ordering.slots[index + 1].film_ids if index + 1 < len(ordering) else ()
    tied = tuple(film_id for film_id in slot.film_ids if film_id != tmdb_id)
    cards = await ordering_module.cards(db, [tmdb_id, *above, *below, *tied])
    rating = bands.band_of_slot(boundaries, index)
    anchors = await anchors_module.current(db, account.id)
    placement = await _placement(db, account_film)
    return PlacementLanded(
        film=cards[tmdb_id],
        position=index + 1,
        total=len(ordering),
        rating=rating,
        band_anchor=anchors.get(rating) == tmdb_id if rating is not None else False,
        provisional=placement.trust is PlacementTrust.provisional,
        anchor_nudge=rating is None and not anchors,
        designated=designated,
        neighbours=Neighbours(
            above=[cards[film_id] for film_id in above],
            tied_with=[cards[film_id] for film_id in tied],
            below=[cards[film_id] for film_id in below],
        ),
    )


# --- Helpers ---


def _seed(account_id: uuid.UUID, tmdb_id: int, given: int | None) -> int:
    """Stable per film, so asking twice without answering offers the same pair."""
    if given is not None:
        return given
    return zlib.crc32(f"{account_id}:{tmdb_id}".encode())


async def _ballpark_opponent(
    db: AsyncSession, account_id: uuid.UUID, ballpark: tuple[float | None, float | None]
) -> int | None:
    """The anchor nearest the owner's hunch, which is the whole of a ballpark's effect.

    Nothing about it is recorded and nothing about it constrains the search: it moves
    one anchor to the front of the queue and then it is gone. A guess that pinned a
    divider would be an absolute rating in disguise (onboarding-and-import.md).
    """
    low, high = ballpark
    if low is None:
        return None
    top, bottom = max(low, high or low), min(low, high or low)
    anchors = await anchors_module.current(db, account_id)
    if not anchors:
        return None
    return min(anchors.items(), key=lambda item: max(bottom - item[0], item[0] - top, 0.0))[1]


def _comparison(
    account_id: uuid.UUID, flow: Flow, subject: int, opponent: int, verdict: ComparisonVerdict
) -> ComparisonLogEntry:
    return ComparisonLogEntry(
        account_id=account_id,
        kind=ComparisonKind.overall,
        subject_film_id=subject,
        film_a_id=subject,
        film_b_id=opponent,
        verdict=verdict,
        context=flow.context,
        status=ComparisonStatus.active,
    )


def _check_answerable(ordering: Ordering, search: Search, opponent: int) -> None:
    """Refuse an answer about a film the current search is not asking about.

    A stale question - answered from a screen left open while the ordering moved on -
    would otherwise write a judgment the bounds cannot hold, and the log is append-only,
    so there would be no taking it back.
    """
    index = ordering.index_of(opponent)
    if index is None:
        raise ApiError(409, "opponent_not_rated", "That film is not in your ordering.")
    if not search.lo <= index < search.hi:
        raise ApiError(409, "stale_question", "That comparison is no longer the one being asked.")


def _check_offered(candidates: tuple[float, ...], band: float) -> None:
    """Refuse a band the question did not offer, for the same reason a stale answer is."""
    if band not in candidates:
        raise ApiError(409, "band_not_offered", "That band is not one of the choices.")


async def _placeable(db: AsyncSession, account: Account, tmdb_id: int) -> AccountFilm:
    """The film's record, refusing anything the owner has not said they watched."""
    account_film: AccountFilm | None = await db.scalar(
        select(AccountFilm).where(
            AccountFilm.account_id == account.id, AccountFilm.film_id == tmdb_id
        )
    )
    if account_film is None or account_film.state is LifecycleState.backlog:
        raise ApiError(409, "not_watched", "Mark this film watched before placing it.")
    return account_film


async def _placement(db: AsyncSession, account_film: AccountFilm) -> Placement:
    placement = await db.scalar(
        select(Placement).where(Placement.account_film_id == account_film.id)
    )
    assert placement is not None  # a rated film is a placed film
    return placement


async def _entries(db: AsyncSession, account_id: uuid.UUID, flow: Flow) -> list[ComparisonLogEntry]:
    """This flow's own comparisons, oldest first: the whole of its state.

    Scoped to the context, so a re-placement starts from what it has been told rather
    than from the judgments that produced the position it is questioning - and to the
    moment the flow began, so a previous re-placement's answers stay with it.
    """
    query = select(ComparisonLogEntry).where(
        ComparisonLogEntry.account_id == account_id,
        ComparisonLogEntry.subject_film_id == flow.film_id,
        ComparisonLogEntry.kind == ComparisonKind.overall,
        ComparisonLogEntry.context == flow.context,
        ComparisonLogEntry.status == ComparisonStatus.active,
    )
    if flow.since is not None:
        query = query.where(ComparisonLogEntry.created_at > flow.since)
    rows = await db.scalars(query.order_by(ComparisonLogEntry.created_at, ComparisonLogEntry.id))
    return list(rows)


def _opponents(entries: list[ComparisonLogEntry], subject: int) -> list[int]:
    """Every film this flow asked about, oldest first and each named once."""
    seen: dict[int, None] = {}
    for entry in entries:
        opponent = entry.film_b_id if entry.film_a_id == subject else entry.film_a_id
        if opponent is not None:
            seen[opponent] = None
    return list(seen)


async def _band_judgment(
    db: AsyncSession, account_id: uuid.UUID, flow: Flow
) -> ComparisonLogEntry | None:
    """The band this flow's owner has already chosen, where the question was asked."""
    query = select(ComparisonLogEntry).where(
        ComparisonLogEntry.account_id == account_id,
        ComparisonLogEntry.subject_film_id == flow.film_id,
        ComparisonLogEntry.context == flow.context,
        ComparisonLogEntry.band.is_not(None),
        ComparisonLogEntry.status == ComparisonStatus.active,
    )
    if flow.since is not None:
        query = query.where(ComparisonLogEntry.created_at > flow.since)
    judgment: ComparisonLogEntry | None = await db.scalar(
        query.order_by(ComparisonLogEntry.created_at.desc(), ComparisonLogEntry.id.desc()).limit(1)
    )
    return judgment


async def _evidence(
    db: AsyncSession, account_id: uuid.UUID, film_id: int
) -> list[ComparisonLogEntry]:
    """Every live comparison touching this film, whoever's flow produced it.

    A comparison run for another film's placement is evidence about this one too - the
    double-duty opponent that pulls a provisional film towards a settled position
    without ever asking the owner an extra question (onboarding-and-import.md).
    """
    rows = await db.scalars(
        select(ComparisonLogEntry)
        .where(
            ComparisonLogEntry.account_id == account_id,
            ComparisonLogEntry.kind == ComparisonKind.overall,
            ComparisonLogEntry.status == ComparisonStatus.active,
            or_(
                ComparisonLogEntry.film_a_id == film_id,
                ComparisonLogEntry.film_b_id == film_id,
            ),
        )
        .order_by(ComparisonLogEntry.created_at, ComparisonLogEntry.id)
    )
    return list(rows)
