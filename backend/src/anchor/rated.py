"""The Rated screen: the ordering grouped into bands, and the rate-later queue below it.

The default view is the ordering best to worst, grouped by the band each slot derives
into, with the half-star value as the group header and the band's anchor badged. A run
of slots the dividers cannot yet decide groups under no band at all - the honest
"rating pending" state a fresh account lives in until designations erect the first
dividers, rather than a zero or a guess.

Every other sort is a flat list, deliberately: recently-rated or by title cuts across
the ordering, and a band header over a sequence that is not in band order would be a
heading over nothing. Filters apply to both, and their menus are computed over the whole
rated set so narrowing never empties the menu that did the narrowing.
"""

import uuid
from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from anchor import anchors as anchors_module
from anchor import bands
from anchor import ordering as ordering_module
from anchor.accounts import CurrentAccount
from anchor.catalog import FilmCard
from anchor.deps import DbSession
from anchor.models import (
    AccountFilm,
    Film,
    LifecycleState,
    Placement,
    PlacementTrust,
    WatchEvent,
)

router = APIRouter(prefix="/api/rated")

RatedSort = Literal["position", "rated", "watched", "title", "year"]
"""Position is the ordering itself; every other sort drops the band grouping."""

DECADE_SPAN = 10


class RatedFilm(BaseModel):
    """One rated film as the screen lists it: what it is, and where it stands."""

    tmdb_id: int
    title: str
    year: int | None
    poster_path: str | None
    genres: list[str]
    position: int
    """1-based rank of the film's slot, best first."""
    band: float | None
    """Derived from position against the dividers; None while its band is undecidable."""
    anchor: bool
    """This film is the canonical exemplar of its band."""
    provisional: bool
    """The ambient settling marker: trusted less until its evidence catches up."""


class BandGroup(BaseModel):
    """One run of the ordering sharing a band, or one run that has no band yet."""

    band: float | None
    slots: list[list[RatedFilm]]
    """Tie-groups, in order; the films in one slot are the ones judged equal."""


class Rated(BaseModel):
    """The screen. Exactly one of ``groups`` and ``films`` is filled, per the sort."""

    sort: RatedSort
    groups: list[BandGroup] | None
    """The banded ordering, for the position sort."""
    films: list[RatedFilm] | None
    """The flat list, for every other sort."""
    bands: list[float]
    genres: list[str]
    decades: list[int]
    """Every value the whole rated set offers, so a filter never empties its own menu."""
    anchor_nudge: bool
    """No anchor exists yet: the one line explaining where the half-stars have gone."""
    rate_later: list[FilmCard]
    """Watched-unrated films seated in the queue, awaiting an optional placement."""


@router.get("")
async def rated(
    account: CurrentAccount,
    db: DbSession,
    sort: RatedSort = "position",
    band_min: Annotated[float | None, Query(ge=0.5, le=5.0)] = None,
    band_max: Annotated[float | None, Query(ge=0.5, le=5.0)] = None,
    genre: Annotated[str | None, Query(max_length=100)] = None,
    decade: Annotated[int | None, Query(ge=1000, le=9990)] = None,
) -> Rated:
    ordering = await ordering_module.load(db, account.id)
    boundaries = await bands.load(db, account.id)
    derived = ordering_module.bands_of(ordering, boundaries)
    anchors = await anchors_module.current(db, account.id)
    films = await _films(db, ordering.all_film_ids())
    provisional = await _provisional(db, account.id)

    rows = [
        (index, _row(film_id, index, films[film_id], derived[film_id], anchors, provisional))
        for index, slot in enumerate(ordering.slots)
        for film_id in slot.film_ids
        if film_id in films
    ]
    kept = [
        row
        for row in rows
        if _passes(row[1], films[row[1].tmdb_id], band_min, band_max, genre, decade)
    ]

    seated = await _rate_later_queue(db, account.id)
    return Rated(
        sort=sort,
        groups=_group(kept) if sort == "position" else None,
        films=None if sort == "position" else await _flatten(db, account.id, kept, sort),
        bands=sorted({row[1].band for row in rows if row[1].band is not None}, reverse=True),
        genres=sorted({name for row in rows for name in films[row[1].tmdb_id].genres}),
        decades=sorted(
            {
                year - year % DECADE_SPAN
                for row in rows
                if (year := films[row[1].tmdb_id].release_year) is not None
            },
            reverse=True,
        ),
        anchor_nudge=not anchors,
        rate_later=_queue(await ordering_module.cards(db, seated), seated),
    )


Row = tuple[int, RatedFilm]
"""A film with the index of the slot it sits in, before any sorting is applied."""


def _row(
    film_id: int,
    index: int,
    stored: Film,
    band: float | None,
    anchors: dict[float, int],
    provisional: set[int],
) -> RatedFilm:
    return RatedFilm(
        tmdb_id=film_id,
        title=stored.title,
        year=stored.release_year,
        poster_path=stored.poster_path,
        genres=stored.genres,
        position=index + 1,
        band=band,
        anchor=band is not None and anchors.get(band) == film_id,
        provisional=film_id in provisional,
    )


def _queue(cards: dict[int, FilmCard], seated: list[int]) -> list[FilmCard]:
    """The rate-later queue in the order it was read, not the order the cards came back."""
    return [cards[film_id] for film_id in seated if film_id in cards]


def _passes(
    film: RatedFilm,
    stored: Film,
    band_min: float | None,
    band_max: float | None,
    genre: str | None,
    decade: int | None,
) -> bool:
    """A band filter excludes films with no band: they have none to fall in the range."""
    if band_min is not None or band_max is not None:
        if film.band is None:
            return False
        if band_min is not None and film.band < band_min:
            return False
        if band_max is not None and film.band > band_max:
            return False
    if genre is not None and genre not in stored.genres:
        return False
    if decade is not None:
        year = stored.release_year
        if year is None or not decade <= year < decade + DECADE_SPAN:
            return False
    return True


def _group(rows: list[Row]) -> list[BandGroup]:
    """Walk the ordering top to bottom, opening a group each time the band changes.

    A run the dividers cannot decide comes back under ``band=None``, which is the
    position-only state said out loud rather than papered over with a value.
    """
    groups: list[BandGroup] = []
    slot: int | None = None
    for index, film in rows:
        if not groups or groups[-1].band != film.band:
            groups.append(BandGroup(band=film.band, slots=[]))
            slot = None
        if index != slot:
            groups[-1].slots.append([])
            slot = index
        groups[-1].slots[-1].append(film)
    return groups


async def _flatten(
    db: AsyncSession,
    account_id: uuid.UUID,
    rows: list[Row],
    sort: RatedSort,
) -> list[RatedFilm]:
    """Every sort but position, tie-broken on title so a listing never reshuffles."""
    placed = await _placed_at(db, account_id)
    watched = await _last_watched(db, account_id)
    keys: dict[str, Callable[[RatedFilm], tuple[Any, ...]]] = {
        "rated": lambda film: (-placed.get(film.tmdb_id, 0.0), film.title),
        "watched": lambda film: (-watched.get(film.tmdb_id, 0.0), film.title),
        "title": lambda film: (film.title,),
        "year": lambda film: (-(film.year or 0), film.title),
    }
    return sorted((row[1] for row in rows), key=keys[sort])


# --- Reads ---


async def _films(db: AsyncSession, film_ids: list[int]) -> dict[int, Film]:
    if not film_ids:
        return {}
    rows = await db.scalars(select(Film).where(Film.tmdb_id.in_(film_ids)))
    return {film.tmdb_id: film for film in rows}


async def _provisional(db: AsyncSession, account_id: uuid.UUID) -> set[int]:
    """Films whose placement is trusted less than a fully-compared one."""
    rows = await db.scalars(
        _placements_of(select(AccountFilm.film_id), account_id).where(
            Placement.trust == PlacementTrust.provisional
        )
    )
    return set(rows)


async def _placed_at(db: AsyncSession, account_id: uuid.UUID) -> dict[int, float]:
    """When each film last landed: the "recently rated" sort's clock."""
    rows = await db.execute(
        _placements_of(select(AccountFilm.film_id, Placement.placed_at), account_id)
    )
    return {film_id: placed.timestamp() for film_id, placed in rows}


def _placements_of(query: Select[Any], account_id: uuid.UUID) -> Select[Any]:
    """One account's placements with their films; the left side is named, not guessed."""
    return (
        query.select_from(Placement)
        .join(AccountFilm, AccountFilm.id == Placement.account_film_id)
        .where(Placement.account_id == account_id)
    )


async def _last_watched(db: AsyncSession, account_id: uuid.UUID) -> dict[int, float]:
    """The latest watch per film. A rated film may have none: an import can rate without one."""
    rows = await db.execute(
        select(WatchEvent.film_id, func.max(WatchEvent.watched_at))
        .where(WatchEvent.account_id == account_id)
        .group_by(WatchEvent.film_id)
    )
    return {film_id: watched.timestamp() for film_id, watched in rows}


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
