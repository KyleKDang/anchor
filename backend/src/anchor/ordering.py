"""The ordering: the explicit persisted sequence of tie-group slots, and how films move in it.

The sequence is primary state (ADR 0001). Nothing here derives it from the comparison
log and nothing here is reachable from the advisory math; every function that writes it
runs only at the end of a flow the owner's own answers settled, and the account-realm
wipe is the only other thing that touches these rows.

Positions are dense and start at 0, best to worst, so opening a slot shifts every slot
below it down by one. That keeps "the film two places above this one" a plain
subtraction, at the cost of one bulk update per placement - the right trade for a
personal library, where placements are rare and reads are constant. Dividers are indices
into the same sequence, so every write here renumbers them in step: they must go on
separating the same two slots they separated before, which is why the slot writers own
that renumbering rather than leaving it to their callers to remember.

Rating is derived, never stored: :func:`bands_of` is the whole of it, reading a slot's
position against the dividers and yielding nothing where the dividers that would decide
it are unpinned.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from anchor import bands
from anchor.bands import Boundaries
from anchor.catalog import FilmCard
from anchor.models import (
    AccountFilm,
    ComparisonLogEntry,
    Film,
    LifecycleState,
    Placement,
    PlacementProvenance,
    PlacementTrust,
    TieGroupSlot,
)


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

    def without(self, film_id: int) -> "Ordering":
        """The ordering as it would read if this film had never been placed.

        What the film's own judgments are re-read against when its position is being
        re-derived, since a film cannot be evidence about where it belongs.
        """
        kept: list[Slot] = []
        for slot in self.slots:
            members = tuple(member for member in slot.film_ids if member != film_id)
            if members:
                kept.append(Slot(id=slot.id, position=len(kept), film_ids=members))
        return Ordering(slots=tuple(kept))


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


# --- Derivation ---


def bands_of(ordering: Ordering, boundaries: Boundaries) -> dict[int, float | None]:
    """Every rated film's half-star band, or None where its band is not yet derivable.

    This is the only place a rating comes from. Nothing stores one, so nothing can go
    stale, and a film whose bracketing dividers are unpinned honestly has no value -
    it shows its position instead until the band structure reaches it.
    """
    return {
        film_id: bands.band_of_slot(boundaries, index)
        for index, slot in enumerate(ordering.slots)
        for film_id in slot.film_ids
    }


async def derived_bands(db: AsyncSession, account_id: uuid.UUID) -> dict[int, float | None]:
    """:func:`bands_of` for callers holding neither the ordering nor the dividers yet."""
    return bands_of(await load(db, account_id), await bands.load(db, account_id))


# --- Writing the sequence ---


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


async def new_slot(
    db: AsyncSession,
    account_id: uuid.UUID,
    index: int,
    *,
    band: float | None = None,
    judgment: ComparisonLogEntry | None = None,
) -> TieGroupSlot:
    """Open a slot at ``index``, pushing everything from there down one position.

    The dividers renumber with it, and the ``band`` the film landed in is what settles
    the one divider a renumbering cannot: one sitting exactly at ``index`` has the new
    slot on neither side of it until an answer says. Passing the ``judgment`` that
    answer produced stamps it on every divider it decided, which is what makes the move
    auditable back to the question the owner was asked.
    """
    boundaries = await bands.load(db, account_id)
    decided = bands.sharpened_by(boundaries, index) if judgment is not None else ()
    shifted = bands.after_insert(boundaries, index, band)
    await bands.renumber(db, account_id, shifted)
    if judgment is not None:
        await bands.move(db, account_id, shifted, {key: shifted[key] for key in decided}, judgment)

    await db.execute(
        update(TieGroupSlot)
        .where(TieGroupSlot.account_id == account_id, TieGroupSlot.position >= index)
        .values(position=TieGroupSlot.position + 1)
    )
    slot = TieGroupSlot(account_id=account_id, position=index)
    db.add(slot)
    await db.flush()
    return slot


async def drop_slot(
    db: AsyncSession, account_id: uuid.UUID, slot_id: uuid.UUID, index: int
) -> None:
    """Close the slot at ``index``, now that the last film in it has moved elsewhere.

    A slot never sits empty, so the one a re-seated film leaves behind goes with it, and
    the dividers renumber back down. Renumbering only: the dividers still separate the
    same films they separated before, and no judgment was made here.
    """
    await bands.renumber(
        db, account_id, bands.after_remove(await bands.load(db, account_id), index)
    )
    await db.execute(delete(TieGroupSlot).where(TieGroupSlot.id == slot_id))
    await db.execute(
        update(TieGroupSlot)
        .where(TieGroupSlot.account_id == account_id, TieGroupSlot.position > index)
        .values(position=TieGroupSlot.position - 1)
    )
    await db.flush()


async def reseat(
    db: AsyncSession,
    account_id: uuid.UUID,
    placement: Placement,
    ordering: Ordering,
    film_id: int,
    *,
    index: int | None = None,
    slot: TieGroupSlot | None = None,
    band: float | None = None,
    judgment: ComparisonLogEntry | None = None,
) -> None:
    """Move an already-placed film: to a slot of its own at ``index``, or into ``slot``.

    ``index`` is an insertion index read against ``ordering.without(film_id)`` - the
    ordering as it reads with this film lifted out of it. That is the same sequence the
    film's own search runs against, since a film is never evidence about where it
    belongs, so callers hand over the index they already have rather than translating.

    The placement's clock restarts here: a re-seat is the film being placed again, and
    "recently rated" is the last placement *or re-placement* (screens-and-flows.md).
    """
    placement.placed_at = func.now()
    old = ordering.index_of(film_id)
    assert old is not None  # only a placed film is ever re-seated
    if len(ordering.slots[old].film_ids) == 1:
        # The film was the whole slot, so the slot goes with it and the sequence
        # closes up - which is exactly what ``without`` said it would look like.
        await drop_slot(db, account_id, ordering.slots[old].id, old)
    if slot is not None:
        placement.slot_id = slot.id
        return
    assert index is not None  # a re-seat names either a slot to join or an index to open
    opened = await new_slot(db, account_id, index, band=band, judgment=judgment)
    placement.slot_id = opened.id


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
