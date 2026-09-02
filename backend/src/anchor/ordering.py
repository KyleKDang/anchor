"""The ordering: the explicit persisted sequence of tie-group slots, and its read surface.

The sequence is primary state (ADR 0001). Nothing here derives it from the comparison
log and nothing here is reachable from the advisory math. Two functions write it,
:func:`new_slot` and :func:`land`, and both run only from the end of a placement the
owner's own answers settled; the account-realm wipe is the only other thing that
touches these rows.

Positions are dense and start at 0, best to worst, so inserting a slot shifts every
slot below it down by one. That keeps "the film two places above this one" a plain
subtraction, at the cost of one bulk update per placement - the right trade for a
personal library, where placements are rare and reads are constant.

Rating is derived, never stored, so nothing here computes one: a slot's position against
the dividers is what a band means, and dividers arrive with #28. Until then the ordering
is honestly position-only.
"""

import uuid
from dataclasses import dataclass

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from anchor.accounts import CurrentAccount
from anchor.catalog import FilmCard
from anchor.deps import DbSession
from anchor.models import (
    AccountFilm,
    Film,
    LifecycleState,
    Placement,
    PlacementProvenance,
    PlacementTrust,
    TieGroupSlot,
)

router = APIRouter(prefix="/api/rated")


@dataclass(frozen=True)
class Slot:
    """One position of the ordering and the films the owner has judged equal there."""

    id: uuid.UUID
    position: int
    film_ids: tuple[int, ...]


@dataclass(frozen=True)
class Ordering:
    """One account's whole ordering, best to worst, as the placement search reads it."""

    slots: tuple[Slot, ...]

    def __len__(self) -> int:
        return len(self.slots)

    def index_of(self, film_id: int) -> int | None:
        """Which slot a rated film sits in, or None where the film is not rated."""
        return next((i for i, slot in enumerate(self.slots) if film_id in slot.film_ids), None)

    def all_film_ids(self) -> list[int]:
        """Every rated film, best slot first. Named apart from ``Slot.film_ids``."""
        return [film_id for slot in self.slots for film_id in slot.film_ids]


async def load(db: AsyncSession, account_id: uuid.UUID) -> Ordering:
    """The account's ordering. One query: a slot is only ever read with its members."""
    rows = await db.execute(
        select(TieGroupSlot.id, TieGroupSlot.position, AccountFilm.film_id)
        .join(Placement, Placement.slot_id == TieGroupSlot.id)
        .join(AccountFilm, AccountFilm.id == Placement.account_film_id)
        .where(TieGroupSlot.account_id == account_id)
        # Within a slot the members are tied, so any stable order will do; oldest first
        # reads as "and then this one joined it".
        .order_by(TieGroupSlot.position, Placement.placed_at, AccountFilm.film_id)
    )
    members: dict[uuid.UUID, list[int]] = {}
    positions: dict[uuid.UUID, int] = {}
    for slot_id, position, film_id in rows:
        members.setdefault(slot_id, []).append(film_id)
        positions[slot_id] = position
    slots = [
        Slot(id=slot_id, position=positions[slot_id], film_ids=tuple(film_ids))
        for slot_id, film_ids in members.items()
    ]
    return Ordering(slots=tuple(sorted(slots, key=lambda slot: slot.position)))


def land(
    db: AsyncSession,
    account_film: AccountFilm,
    *,
    slot: TieGroupSlot,
    provenance: PlacementProvenance = PlacementProvenance.completed,
) -> Placement:
    """Seat a film in a slot, which is what makes it rated.

    The rate-later seat goes with it: the seat is meaningful only while a film is
    watched-unrated, and a placed film is not.
    """
    account_film.state = LifecycleState.rated
    account_film.rate_later = False
    placement = Placement(
        account_id=account_film.account_id,
        account_film_id=account_film.id,
        slot_id=slot.id,
        trust=(
            PlacementTrust.full
            if provenance is PlacementProvenance.completed
            else PlacementTrust.provisional
        ),
        provenance=provenance,
    )
    db.add(placement)
    return placement


async def new_slot(db: AsyncSession, account_id: uuid.UUID, index: int) -> TieGroupSlot:
    """Open a slot at ``index``, pushing everything from there down one position."""
    await db.execute(
        update(TieGroupSlot)
        .where(TieGroupSlot.account_id == account_id, TieGroupSlot.position >= index)
        .values(position=TieGroupSlot.position + 1)
    )
    slot = TieGroupSlot(account_id=account_id, position=index)
    db.add(slot)
    await db.flush()
    return slot


async def slot_by_id(db: AsyncSession, slot_id: uuid.UUID) -> TieGroupSlot:
    slot = await db.get(TieGroupSlot, slot_id)
    assert slot is not None  # read straight out of the ordering this request just loaded
    return slot


async def cards(db: AsyncSession, film_ids: list[int]) -> dict[int, FilmCard]:
    """Film cards for a batch of ids, in one query rather than one per film."""
    if not film_ids:
        return {}
    films = await db.scalars(select(Film).where(Film.tmdb_id.in_(film_ids)))
    return {film.tmdb_id: FilmCard.of(film) for film in films}


# --- The Rated screen ---


class RatedSlot(BaseModel):
    """One row of the ordering: its rank, and the films tied there."""

    position: int
    """1-based rank among slots, best first. Not a rating: bands arrive with #28."""
    films: list[FilmCard]


class Rated(BaseModel):
    ordering: list[RatedSlot]
    rate_later: list[FilmCard]
    """Watched-unrated films seated in the queue, awaiting an optional placement."""


@router.get("")
async def rated(account: CurrentAccount, db: DbSession) -> Rated:
    """The Rated screen: the position-only ordering, and the rate-later queue below it.

    Band grouping, anchors, and the needs-attention strip belong to later tickets; with
    no dividers pinned, grouping by band would be grouping by nothing.
    """
    ordering = await load(db, account.id)
    seated = await _rate_later_queue(db, account.id)
    by_id = await cards(db, ordering.all_film_ids() + seated)
    return Rated(
        ordering=[
            RatedSlot(
                position=rank,
                films=[by_id[film_id] for film_id in slot.film_ids if film_id in by_id],
            )
            for rank, slot in enumerate(ordering.slots, start=1)
        ],
        rate_later=[by_id[film_id] for film_id in seated if film_id in by_id],
    )


async def _rate_later_queue(db: AsyncSession, account_id: uuid.UUID) -> list[int]:
    rows = await db.scalars(
        select(AccountFilm.film_id)
        .where(
            AccountFilm.account_id == account_id,
            AccountFilm.state == LifecycleState.watched_unrated,
            AccountFilm.rate_later.is_(True),
        )
        .order_by(AccountFilm.added_at.desc(), AccountFilm.film_id)
    )
    return list(rows)
