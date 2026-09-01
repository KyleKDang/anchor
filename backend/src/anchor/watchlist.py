"""The Watchlist screen's backlog half: the unwatched films the owner has added.

The ranked tier sits above this and arrives with #33; before taste-profile readiness
the screen is honestly just the backlog, so that is all this serves.

Its sorts are recently-added, title, and year - and deliberately not engine score.
ADR 0005 bars anything rating-shaped on unwatched films, and a score-ordered backlog
would quietly become a second, undamped ranked tier. The sort parameter is a closed
set, so asking for a score sort is refused rather than silently ignored.
"""

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import ColumnElement, and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import UnaryExpression

from anchor.accounts import CurrentAccount
from anchor.catalog import BacklogFilm
from anchor.deps import DbSession
from anchor.models import AccountFilm, Film, LifecycleState

router = APIRouter(prefix="/api/watchlist")

BacklogSort = Literal["added", "title", "year"]
"""Every sort the backlog offers. "score" is absent on purpose (ADR 0005)."""

DECADE_SPAN = 10


class Backlog(BaseModel):
    """The backlog, plus the filter values the whole backlog offers to choose from."""

    films: list[BacklogFilm]
    genres: list[str]
    decades: list[int]


@router.get("/backlog")
async def backlog(
    account: CurrentAccount,
    db: DbSession,
    sort: BacklogSort = "added",
    genre: Annotated[str | None, Query(max_length=100)] = None,
    decade: Annotated[int | None, Query(ge=1000, le=9990)] = None,
) -> Backlog:
    rows = await db.execute(
        select(Film, AccountFilm)
        .join(AccountFilm, AccountFilm.film_id == Film.tmdb_id)
        .where(_in_backlog(account.id), *_filters(genre, decade))
        .order_by(*_ordering(sort))
    )
    films = [BacklogFilm.of(film, account_film) for film, account_film in rows]
    return Backlog(
        films=films,
        genres=await _available_genres(db, account.id),
        decades=await _available_decades(db, account.id),
    )


def _in_backlog(account_id: uuid.UUID) -> ColumnElement[bool]:
    return and_(AccountFilm.account_id == account_id, AccountFilm.state == LifecycleState.backlog)


def _filters(genre: str | None, decade: int | None) -> list[ColumnElement[bool]]:
    filters: list[ColumnElement[bool]] = []
    if genre is not None:
        # `@>` on the text[] column: the backlog's genres contain the one asked for.
        filters.append(Film.genres.contains([genre]))
    if decade is not None:
        filters.append(Film.release_year.between(decade, decade + DECADE_SPAN - 1))
    return filters


def _ordering(sort: BacklogSort) -> list[UnaryExpression[Any]]:
    """Every sort breaks its ties on title, so a listing never reshuffles between calls."""
    by_title = Film.title.asc()
    if sort == "title":
        return [by_title]
    if sort == "year":
        return [Film.release_year.desc().nullslast(), by_title]
    return [AccountFilm.added_at.desc(), by_title]


async def _available_genres(db: AsyncSession, account_id: uuid.UUID) -> list[str]:
    """Every genre present in the whole backlog, so filtering never empties its own menu."""
    rows = await db.scalars(
        select(Film.genres)
        .join(AccountFilm, AccountFilm.film_id == Film.tmdb_id)
        .where(_in_backlog(account_id))
    )
    return sorted({genre for genres in rows for genre in genres})


async def _available_decades(db: AsyncSession, account_id: uuid.UUID) -> list[int]:
    rows = await db.scalars(
        select(Film.release_year)
        .join(AccountFilm, AccountFilm.film_id == Film.tmdb_id)
        .where(_in_backlog(account_id), Film.release_year.is_not(None))
    )
    return sorted({year - year % DECADE_SPAN for year in rows if year is not None}, reverse=True)
