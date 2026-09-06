"""The sync list: the films the owner would have to retype on Letterboxd today.

Anchor never writes to Letterboxd and never will, so the one rating set living in two
places is kept in step by hand (surfacing.md). What this module owes the owner is the
smallest honest answer to "what is out of date over there": each film, what Letterboxd
holds, and what Anchor holds now.

*The list is derived, never stored.* There is no queue of pending syncs and nothing marks
a film as needing one. Every rated film carries a last synced rating - the import writes
it once, and the owner marking a film synced is the only other thing that ever does - and
the list is simply the rated films whose band has moved off it, plus the ones that never
had one because Letterboxd never saw them. A rating that wobbles back to its synced value
therefore drops off by itself: the difference the list is made of is gone, and nothing
has to notice.

*Nothing is held back.* Every rated film has a band the owner chose, so every rated film
has a value worth carrying over, and there is no such thing as a rating Anchor has not
finished making up its own mind about (ADR 0013). The list is still empty right after an
import - the import writes the synced value it read from the export - and it fills only
as the owner corrects the wall, which is exactly what it is for.

Ambient by ADR 0011: the list is at its one home in Profile's Letterboxd area, it is a
count and a list rather than a reminder, and nothing about it is ever mentioned elsewhere.
"""

import uuid

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from anchor import ordering as ordering_module
from anchor.accounts import CurrentAccount
from anchor.deps import DbSession
from anchor.errors import ApiError
from anchor.models import AccountFilm, Film, LifecycleState, Placement

router = APIRouter(prefix="/api/sync")


class SyncFilm(BaseModel):
    """One film the two sides disagree about, said as old → new."""

    tmdb_id: int
    title: str
    year: int | None
    poster_path: str | None
    synced: float | None
    """What Letterboxd holds, as far as Anchor knows; None where it never saw the film."""
    band: float
    """What Anchor holds now - the value the owner would type over there."""


class SyncList(BaseModel):
    """The whole area, in the two shapes the owner reads differently.

    A film Letterboxd rated and a film it has never heard of are different errands - one
    is an edit and the other is a new entry - so they are separate sections rather than
    one list with a blank in it. ``count`` is both of them, because the ambient count at
    the entry point is a count of the work, not of one shape of it.
    """

    changed: list[SyncFilm]
    never_recorded: list[SyncFilm]
    count: int


@router.get("")
async def sync_list(account: CurrentAccount, db: DbSession) -> SyncList:
    """Every film whose Letterboxd value is out of date, best first.

    Best first because that is the order the owner already reads their ratings in on
    Rated; the list is a working surface to type from, and a second ordering to learn
    would buy nothing.
    """
    entries = await _entries(db, account.id)
    films = await _films(db, [account_film.film_id for account_film, _ in entries])
    rows = [
        _row(films[account_film.film_id], account_film.last_synced_rating, band)
        for account_film, band in entries
        if account_film.film_id in films
    ]
    return SyncList(
        changed=[row for row in rows if row.synced is not None],
        never_recorded=[row for row in rows if row.synced is None],
        count=len(rows),
    )


# Declared ahead of the per-film route so "all" is read as itself rather than as a film
# id that fails to parse.
@router.post("/all", status_code=204)
async def mark_all_synced(account: CurrentAccount, db: DbSession) -> None:
    """The owner saying they have carried the whole list over in one sitting.

    Each film records the band it holds at this moment, which is the value the owner just
    typed. A film re-rated between the read and this write joins the list again on the
    next read rather than being swept up silently.
    """
    for account_film, band in await _entries(db, account.id):
        account_film.last_synced_rating = band
    await db.commit()


@router.post("/{tmdb_id}", status_code=204)
async def mark_synced(tmdb_id: int, account: CurrentAccount, db: DbSession) -> None:
    """Record that Letterboxd now holds what Anchor holds for this one film.

    Deliberately not "is this film on the list?": a film already in step is accepted and
    re-records the same value, so a double tap, or a per-film mark racing a mark-all, is
    a no-op rather than an error the owner has to read. What is refused is a film with no
    rating at all, because there the write would invent a baseline out of nothing.
    """
    found = await _synceable(db, account.id, tmdb_id)
    if found is None:
        raise ApiError(409, "nothing_to_sync", "That film has no rating to carry over.")
    account_film, band = found
    account_film.last_synced_rating = band
    await db.commit()


# --- The derivation ---


async def _entries(db: AsyncSession, account_id: uuid.UUID) -> list[tuple[AccountFilm, float]]:
    """The list itself, as rows to write to rather than rows to render, best first."""
    return [
        (account_film, band)
        for account_film, band in await _carriable(db, account_id)
        if account_film.last_synced_rating != band
    ]


async def _synceable(
    db: AsyncSession, account_id: uuid.UUID, film_id: int
) -> tuple[AccountFilm, float] | None:
    """One film's row, whether or not it currently differs from what Letterboxd holds."""
    return next(
        (found for found in await _carriable(db, account_id) if found[0].film_id == film_id),
        None,
    )


async def _carriable(db: AsyncSession, account_id: uuid.UUID) -> list[tuple[AccountFilm, float]]:
    """Every rated film paired with the band the owner put it in, best first.

    No exclusions: a rated film's band is a value the owner chose, so it is always a
    value they could type into Letterboxd.
    """
    ordering = await ordering_module.load(db, account_id)
    rated = await _rated(db, account_id)
    return [
        (rated[placed.film_id], placed.band)
        for band in ordering.bands()
        for placed in ordering.row(band)
        if placed.film_id in rated
    ]


async def _rated(db: AsyncSession, account_id: uuid.UUID) -> dict[int, AccountFilm]:
    rows = await db.scalars(
        select(AccountFilm)
        .join(Placement, Placement.account_film_id == AccountFilm.id)
        .where(
            AccountFilm.account_id == account_id,
            AccountFilm.state == LifecycleState.rated,
        )
    )
    return {account_film.film_id: account_film for account_film in rows}


async def _films(db: AsyncSession, film_ids: list[int]) -> dict[int, Film]:
    if not film_ids:
        return {}
    rows = await db.scalars(select(Film).where(Film.tmdb_id.in_(film_ids)))
    return {film.tmdb_id: film for film in rows}


def _row(film: Film, synced: float | None, band: float) -> SyncFilm:
    return SyncFilm(
        tmdb_id=film.tmdb_id,
        title=film.title,
        year=film.release_year,
        poster_path=film.poster_path,
        synced=synced,
        band=band,
    )
