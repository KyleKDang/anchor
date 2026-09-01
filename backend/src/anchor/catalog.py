"""The shared film store: TMDB metadata cached per film, and the shapes it goes out in.

The store is shared across every account and carries no ownership, so account
operations never touch it (data-model.md). A film enters it through exactly one
bundled TMDB call and is stamped with the time of that call; anything older than
the refresh window is re-fetched on next use, and the rolling re-sync in
``jobs`` keeps still-referenced films fresh without waiting to be asked.

ADR 0005 governs what leaves here: ``rating`` is filled only for a film the owner
has actually rated, and no rating-shaped value ever appears for an unwatched one.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel
from sqlalchemy import exists, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from anchor.errors import ApiError
from anchor.models import AccountFilm, Film, LifecycleState
from anchor.tmdb import FilmBundle, FilmNotInTmdb, SearchHit, Tmdb, TmdbUnavailable

# --- Wire shapes ---


def derived_rating(account_film: AccountFilm | None) -> float | None:
    """A rated film's half-star value, derived from its position against the dividers.

    Ratings are never stored, only derived (data-model.md), and the positions and
    dividers they derive from arrive with the ordering (#27) and the bands (#28).
    Until then a rated film honestly has no value to show, and every surface here
    already renders that absence.
    """
    return None


class SearchResult(BaseModel):
    """A TMDB search row, flagged with what the owner already knows about the film."""

    tmdb_id: int
    title: str
    year: int | None
    overview: str
    poster_path: str | None
    state: LifecycleState | None
    rating: float | None

    @classmethod
    def of(cls, hit: SearchHit, account_film: AccountFilm | None) -> "SearchResult":
        return cls(
            tmdb_id=hit.tmdb_id,
            title=hit.title,
            year=hit.year,
            overview=hit.overview,
            poster_path=hit.poster_path,
            state=account_film.state if account_film else None,
            rating=derived_rating(account_film),
        )


class FilmDetail(BaseModel):
    """The film page: the stored bundle plus the film's standing in this account."""

    tmdb_id: int
    title: str
    year: int | None
    overview: str
    poster_path: str | None
    backdrop_path: str | None
    runtime: int | None
    genres: list[str]
    directors: list[str]
    cast: list[str]
    vote_average: float
    vote_count: int
    state: LifecycleState | None
    rating: float | None

    @classmethod
    def of(cls, film: Film, account_film: AccountFilm | None) -> "FilmDetail":
        return cls(
            tmdb_id=film.tmdb_id,
            title=film.title,
            year=film.release_year,
            overview=film.overview,
            poster_path=film.poster_path,
            backdrop_path=film.backdrop_path,
            runtime=film.runtime,
            genres=list(film.genres),
            directors=_names(film, "directors"),
            cast=_names(film, "cast"),
            vote_average=film.vote_average,
            vote_count=film.vote_count,
            state=account_film.state if account_film else None,
            rating=derived_rating(account_film),
        )


class BacklogFilm(BaseModel):
    """A backlog row. Every film here is unwatched, so nothing rating-shaped exists."""

    tmdb_id: int
    title: str
    year: int | None
    poster_path: str | None
    genres: list[str]
    added_at: datetime

    @classmethod
    def of(cls, film: Film, account_film: AccountFilm) -> "BacklogFilm":
        return cls(
            tmdb_id=film.tmdb_id,
            title=film.title,
            year=film.release_year,
            poster_path=film.poster_path,
            genres=list(film.genres),
            added_at=account_film.added_at,
        )


def _names(film: Film, role: str) -> list[str]:
    return [str(person["name"]) for person in film.credits.get(role, [])]


# --- The store ---


async def search(tmdb: Tmdb, query: str) -> list[SearchHit]:
    async with _translated_errors():
        return await tmdb.search(query)


async def ensure_film(db: AsyncSession, tmdb: Tmdb, tmdb_id: int, refresh_days: int) -> Film:
    """The store's row for a film, fetched when it is missing and re-fetched when stale."""
    film = await db.get(Film, tmdb_id)
    if film is not None and not _is_stale(film, refresh_days):
        return film
    async with _translated_errors():
        bundle = await tmdb.film(tmdb_id)
    return await store(db, bundle)


async def store(db: AsyncSession, bundle: FilmBundle) -> Film:
    """Write the bundle into the shared store, stamping the time of the call it came from."""
    values = {
        "tmdb_id": bundle.tmdb_id,
        "title": bundle.title,
        "release_year": bundle.year,
        "overview": bundle.overview,
        "poster_path": bundle.poster_path,
        "backdrop_path": bundle.backdrop_path,
        "runtime": bundle.runtime,
        "genres": bundle.genres,
        "keywords": bundle.keywords,
        "credits": bundle.credits,
        "vote_average": bundle.vote_average,
        "vote_count": bundle.vote_count,
        "fetched_at": datetime.now(UTC),
    }
    # Upsert rather than read-then-write: two accounts opening the same film page at
    # once must not race into a duplicate-key failure.
    statement = (
        insert(Film)
        .values(**values)
        .on_conflict_do_update(
            index_elements=[Film.tmdb_id],
            set_={key: value for key, value in values.items() if key != "tmdb_id"},
        )
    )
    await db.execute(statement)
    await db.commit()
    # populate_existing, because a refresh writes over a film this session already loaded,
    # and the identity map would otherwise hand the caller back the row it just replaced.
    film = await db.get(Film, bundle.tmdb_id, populate_existing=True)
    assert film is not None  # written just above, inside this session's own transaction
    return film


def _is_stale(film: Film, refresh_days: int) -> bool:
    return film.fetched_at < _cutoff(refresh_days)


async def stale_referenced_films(db: AsyncSession, refresh_days: int) -> list[int]:
    """Films past the refresh window that some account still tracks.

    Unreferenced rows are deliberately left alone: nobody is looking at them, and
    they re-fetch on next use anyway, so refreshing them would spend TMDB calls on
    films no account has.
    """
    referenced = exists().where(AccountFilm.film_id == Film.tmdb_id)
    rows = await db.scalars(
        select(Film.tmdb_id).where(Film.fetched_at < _cutoff(refresh_days), referenced)
    )
    return list(rows)


def _cutoff(refresh_days: int) -> datetime:
    return datetime.now(UTC) - timedelta(days=refresh_days)


# --- Errors ---


@asynccontextmanager
async def _translated_errors() -> AsyncIterator[None]:
    """Turns the client's failures into the API's error shape."""
    try:
        yield
    except FilmNotInTmdb as error:
        raise ApiError(404, "film_not_found", "We could not find that film on TMDB.") from error
    except TmdbUnavailable as error:
        raise ApiError(
            503, "tmdb_unavailable", "Film data is unavailable right now; try again soon."
        ) from error
