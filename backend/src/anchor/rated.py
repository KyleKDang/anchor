"""The Rated screen: the ordering as ten band rows, and the rate-later queue below it.

The default view is the wall - best band first, the half-star value as each row's header
with the count of that band's anchors, and the rank stamped on every poster. It is the
ordering read back exactly as it is stored, because the ordering is band rows: there is
no derivation here and nothing that can be out of step with what the owner sees.

Every other sort is a flat list, deliberately: recently-rated or by title cuts across
the bands, and a band header over a sequence that is not in band order would be a heading
over nothing. Filters apply to both, and their menus are computed over the whole rated
set so narrowing never empties the menu that did the narrowing.

The screen is a pull surface through and through (surfacing.md). No film is marked as
wanting attention and no move is suggested; what the owner sees is their ordering, and
the way to change it is to move a film.
"""

import uuid
from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from anchor import anchors as anchors_module
from anchor import ordering as ordering_module
from anchor.accounts import CurrentAccount
from anchor.catalog import FilmCard
from anchor.deps import DbSession
from anchor.models import AccountFilm, Film, LifecycleState, Placement, WatchEvent

router = APIRouter(prefix="/api/rated")

RatedSort = Literal["position", "rated", "watched", "title", "year"]
"""Position is the ordering itself; every other sort drops the band rows."""

DECADE_SPAN = 10


class RatedFilm(BaseModel):
    """One rated film as the screen lists it: what it is, and where it stands."""

    tmdb_id: int
    title: str
    year: int | None
    poster_path: str | None
    genres: list[str]
    band: float
    rank: int
    """Position within the band, 1 the best. Stamped on the poster."""
    anchor: bool
    """The owner has marked this film as one they are certain of."""


class BandRow(BaseModel):
    """One band of the wall: its films in rank order, and what its header says."""

    band: float
    films: list[RatedFilm]
    anchors: int
    """The count of the band's anchors, carried on the header (screens-and-flows.md).

    Counted over the whole band rather than over the filtered films, because the header
    is a fact about the band and a filter is a way of looking at it.
    """


class Rated(BaseModel):
    """The screen. Exactly one of ``rows`` and ``films`` is filled, per the sort."""

    sort: RatedSort
    rows: list[BandRow] | None
    """The wall, for the position sort. A band holding nothing is left out."""
    films: list[RatedFilm] | None
    """The flat list, for every other sort."""
    bands: list[float]
    genres: list[str]
    decades: list[int]
    """Every value the whole rated set offers, so a filter never empties its own menu."""
    anchor_nudge: bool
    """The account has no anchors at all: the one line saying what marking one does."""
    rate_later: list[FilmCard]
    """Watched-unrated films seated in the queue, awaiting an optional rating."""


@router.get("")
async def rated(
    account: CurrentAccount,
    db: DbSession,
    sort: RatedSort = "position",
    band_min: Annotated[float | None, Query(ge=0.5, le=5.0)] = None,
    band_max: Annotated[float | None, Query(ge=0.5, le=5.0)] = None,
    genre: Annotated[str | None, Query(max_length=100)] = None,
    decade: Annotated[int | None, Query(ge=1000, le=9990)] = None,
    anchors_only: bool = False,
) -> Rated:
    ordering = await ordering_module.load(db, account.id)
    films = await _films(db, ordering.all_film_ids())
    counts = await anchors_module.counts(db, account.id)

    rows = [
        _row(placed, films[placed.film_id])
        for band in ordering.bands()
        for placed in ordering.row(band)
        if placed.film_id in films
    ]
    kept = [
        row
        for row in rows
        if _passes(row, films[row.tmdb_id], band_min, band_max, genre, decade, anchors_only)
    ]

    seated = await _rate_later_queue(db, account.id)
    cards = await ordering_module.cards(db, seated)
    return Rated(
        sort=sort,
        rows=_wall(kept, counts) if sort == "position" else None,
        films=None if sort == "position" else await _flatten(db, account.id, kept, sort),
        bands=sorted({row.band for row in rows}, reverse=True),
        genres=sorted({name for row in rows for name in films[row.tmdb_id].genres}),
        decades=sorted(
            {
                year - year % DECADE_SPAN
                for row in rows
                if (year := films[row.tmdb_id].release_year) is not None
            },
            reverse=True,
        ),
        anchor_nudge=not counts,
        rate_later=[cards[film_id] for film_id in seated if film_id in cards],
    )


def _row(placed: ordering_module.Placed, stored: Film) -> RatedFilm:
    return RatedFilm(
        tmdb_id=placed.film_id,
        title=stored.title,
        year=stored.release_year,
        poster_path=stored.poster_path,
        genres=stored.genres,
        band=placed.band,
        rank=placed.rank,
        anchor=placed.anchored,
    )


def _passes(
    film: RatedFilm,
    stored: Film,
    band_min: float | None,
    band_max: float | None,
    genre: str | None,
    decade: int | None,
    anchors_only: bool,
) -> bool:
    if anchors_only and not film.anchor:
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


def _wall(rows: list[RatedFilm], counts: dict[float, int]) -> list[BandRow]:
    """The films grouped into their bands, best band first, ranks left as they stand.

    A filter thins a row without renumbering it: the rank on a poster is the film's
    place in its band, and a filtered view that renumbered would be showing the owner a
    position no film actually holds.
    """
    grouped: dict[float, list[RatedFilm]] = {}
    for row in rows:
        grouped.setdefault(row.band, []).append(row)
    return [
        BandRow(band=band, films=grouped[band], anchors=counts.get(band, 0))
        for band in sorted(grouped, reverse=True)
    ]


async def _flatten(
    db: AsyncSession,
    account_id: uuid.UUID,
    rows: list[RatedFilm],
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
    return sorted(rows, key=keys[sort])


# --- Reads ---


async def _films(db: AsyncSession, film_ids: list[int]) -> dict[int, Film]:
    if not film_ids:
        return {}
    rows = await db.scalars(select(Film).where(Film.tmdb_id.in_(film_ids)))
    return {film.tmdb_id: film for film in rows}


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
