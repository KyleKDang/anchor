"""The placement flow: finding a new film's slot in the ordering through comparisons.

One question at a time, four answers - A, B, Tied, Skip - and the search bisects the
ordering between them. Three things make this flow what it is:

*The search has no state of its own.* An in-progress placement is exactly its answers,
which are already in the append-only comparison log, so every step re-derives the search
bounds from that log. Abandoning is therefore free (nothing to clean up) and resuming is
automatic: a later attempt reads the same answers and picks up where the owner left off.

*Opponent selection is advisory only* (ADR 0001). It picks which film to ask about and
nothing else; every bound the search holds came from an owner's answer. It samples, so
it takes a seed, and a scripted answer sequence lands deterministically (testing.md).

*Nothing rating-shaped leaves here mid-flow.* The owner answers on the pure
which-is-better instinct, uncontaminated by the opponent's band, so the values are absent
from the payload rather than hidden by the client. There is no undo, by design: the log
is append-only and re-placement is the correction path.

Sliver questions, the ballpark guess, and the early bail all need bands to exist, so they
arrive with #28; placements land position-only until then, as the spec allows.
"""

import random
import uuid
import zlib
from dataclasses import dataclass
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from anchor import ordering as ordering_module
from anchor.accounts import CurrentAccount
from anchor.catalog import FilmCard, derived_rating
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
)
from anchor.ordering import Ordering

router = APIRouter(prefix="/api/placements")


# --- The search, derived from the log ---


@dataclass(frozen=True)
class Search:
    """Where the owner's answers so far have narrowed the film's landing to.

    ``lo`` and ``hi`` are slot indices bounding the landing inclusively: the film sits
    somewhere in ``[lo, hi]``, and ``lo == hi`` means the search is over and the film
    belongs in a new slot at that index.
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
        return self.tied_with is not None or self.lo == self.hi

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


def choose_opponent(ordering: Ordering, search: Search, seed: int) -> int | None:
    """The advisory pick: which film to ask about next, or None when none is left.

    Bisection wants the midpoint of the live range, so candidates are ranked by distance
    from it and the seeded generator only breaks ties - between the slot above and the
    slot below, and between the members of one tie-group. A skipped film drops out, which
    is how Skip swaps in another opponent. Preferring confidently-placed pivots is a
    no-op while every placement is fully trusted; it starts mattering with #29's imports.
    """
    rng = random.Random(seed)
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
    """One step of the flow: two films, and nothing that could bias the answer."""

    done: Literal[False] = False
    a: FilmCard
    """Always the film being placed."""
    b: FilmCard
    """The opponent the advisory math chose."""
    answered: int


class Neighbours(BaseModel):
    """A landed film's immediate surroundings in the ordering."""

    above: list[FilmCard]
    tied_with: list[FilmCard]
    below: list[FilmCard]


class PlacementLanded(BaseModel):
    """The done screen: where the film landed, and who it landed between."""

    done: Literal[True] = True
    film: FilmCard
    position: int
    """1-based rank of the film's slot, best first."""
    total: int
    rating: float | None
    """Derived from position against the dividers, so nothing until #28 pins them."""
    neighbours: Neighbours


PlacementStep = PlacementQuestion | PlacementLanded

Seed = Annotated[int | None, Query(ge=0)]
"""Overrides the advisory seed, so a scripted answer sequence lands deterministically."""


class Answer(BaseModel):
    """One judgment. ``a`` means the film being placed won; ``b`` means the opponent did."""

    opponent_tmdb_id: int
    verdict: ComparisonVerdict
    seed: int | None = None


# --- The flow ---


@router.post("/{tmdb_id}")
async def begin(
    tmdb_id: int, account: CurrentAccount, db: DbSession, seed: Seed = None
) -> PlacementStep:
    """Start or resume placing a watched-unrated film; a rated one just shows where it sits.

    Beginning seats the film in the rate-later queue, which is what makes abandonment
    safe: the owner can walk away at any point and the film is waiting for them, with
    every answer they gave still standing.
    """
    account_film = await _placeable(db, account, tmdb_id)
    if account_film.state is LifecycleState.rated:
        return await _landed(db, account, account_film)
    account_film.rate_later = True
    return await _advance(db, account, account_film, seed)


@router.post("/{tmdb_id}/answers")
async def answer(
    tmdb_id: int, body: Answer, account: CurrentAccount, db: DbSession
) -> PlacementStep:
    """Record one judgment and ask the next question, or land the film."""
    account_film = await _placeable(db, account, tmdb_id)
    if account_film.state is LifecycleState.rated:
        raise ApiError(409, "already_rated", "You have already placed this film.")
    ordering = await ordering_module.load(db, account.id)
    search = derive(tmdb_id, ordering, await _entries(db, account.id, tmdb_id))
    _check_answerable(ordering, search, body.opponent_tmdb_id)
    db.add(
        ComparisonLogEntry(
            account_id=account.id,
            kind=ComparisonKind.overall,
            subject_film_id=tmdb_id,
            film_a_id=tmdb_id,
            film_b_id=body.opponent_tmdb_id,
            verdict=body.verdict,
            context=ComparisonContext.placement,
            status=ComparisonStatus.active,
        )
    )
    await db.flush()
    return await _advance(db, account, account_film, body.seed)


async def _advance(
    db: AsyncSession, account: Account, account_film: AccountFilm, seed: int | None
) -> PlacementStep:
    """Land the film if the answers have settled it, otherwise ask the next question."""
    tmdb_id = account_film.film_id
    ordering = await ordering_module.load(db, account.id)
    search = derive(tmdb_id, ordering, await _entries(db, account.id, tmdb_id))

    if search.tied_with is not None:
        index = ordering.index_of(search.tied_with)
        assert index is not None  # derive only ties against a film it found in the ordering
        slot = await ordering_module.slot_by_id(db, ordering.slots[index].id)
    elif (
        search.settled
        or (opponent := _next_opponent(ordering, search, account, tmdb_id, seed)) is None
    ):
        # Either the bounds have closed on one index, or every film still in range has
        # been skipped and there is no question left to ask. Both land the film at an
        # index consistent with every judgment given; the midpoint is that index when
        # the range never fully closed.
        slot = await ordering_module.new_slot(db, account.id, search.midpoint)
    else:
        await db.commit()
        return await _question(db, tmdb_id, opponent, search.answered)

    await ordering_module.land(db, account_film, slot=slot)
    await db.commit()
    return await _landed(db, account, account_film)


def _next_opponent(
    ordering: Ordering, search: Search, account: Account, tmdb_id: int, seed: int | None
) -> int | None:
    return choose_opponent(ordering, search, _seed(account.id, tmdb_id, seed))


def _seed(account_id: uuid.UUID, tmdb_id: int, given: int | None) -> int:
    """Stable per film, so asking twice without answering offers the same pair."""
    if given is not None:
        return given
    return zlib.crc32(f"{account_id}:{tmdb_id}".encode())


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


async def _question(
    db: AsyncSession, tmdb_id: int, opponent: int, answered: int
) -> PlacementQuestion:
    cards = await ordering_module.cards(db, [tmdb_id, opponent])
    return PlacementQuestion(a=cards[tmdb_id], b=cards[opponent], answered=answered)


async def _landed(db: AsyncSession, account: Account, account_film: AccountFilm) -> PlacementLanded:
    tmdb_id = account_film.film_id
    ordering = await ordering_module.load(db, account.id)
    index = ordering.index_of(tmdb_id)
    assert index is not None  # the film was just placed, inside this request
    slot = ordering.slots[index]
    above = ordering.slots[index - 1].film_ids if index > 0 else ()
    below = ordering.slots[index + 1].film_ids if index + 1 < len(ordering) else ()
    tied = tuple(film_id for film_id in slot.film_ids if film_id != tmdb_id)
    cards = await ordering_module.cards(db, [tmdb_id, *above, *below, *tied])
    return PlacementLanded(
        film=cards[tmdb_id],
        position=index + 1,
        total=len(ordering),
        rating=derived_rating(account_film),
        neighbours=Neighbours(
            above=[cards[film_id] for film_id in above],
            tied_with=[cards[film_id] for film_id in tied],
            below=[cards[film_id] for film_id in below],
        ),
    )


# --- Helpers ---


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


async def _entries(
    db: AsyncSession, account_id: uuid.UUID, tmdb_id: int
) -> list[ComparisonLogEntry]:
    """This placement's answers, oldest first: the whole of its state."""
    rows = await db.scalars(
        select(ComparisonLogEntry)
        .where(
            ComparisonLogEntry.account_id == account_id,
            ComparisonLogEntry.subject_film_id == tmdb_id,
            ComparisonLogEntry.kind == ComparisonKind.overall,
            ComparisonLogEntry.context == ComparisonContext.placement,
            ComparisonLogEntry.status == ComparisonStatus.active,
        )
        .order_by(ComparisonLogEntry.created_at, ComparisonLogEntry.id)
    )
    return list(rows)
