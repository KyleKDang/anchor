"""Search, the film page, and the backlog transitions that run from either.

Every endpoint here is film-scoped. Search reads TMDB and flags the rows the owner
already knows; opening a film page or adding a film fills the shared store, which is
the only thing that spends a TMDB call per film.

A film's lifecycle state is exclusive and untracked films have no record at all, so
these transitions create the record on the way in and delete it on the way back out.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from anchor import catalog
from anchor.accounts import CurrentAccount
from anchor.catalog import FilmDetail, SearchResult
from anchor.deps import AppSettings, AppTmdb, DbSession
from anchor.errors import ApiError
from anchor.models import (
    Account,
    AccountFilm,
    LifecycleState,
    WatchEvent,
    WatchOrigin,
    WatchStanding,
)

router = APIRouter(prefix="/api/films")

SEARCH_QUERY_MAX = 200


class SearchResults(BaseModel):
    results: list[SearchResult]


class MarkWatched(BaseModel):
    """Logging a watch is always a choice between rating it now and rating it later."""

    rate: Literal["now", "later"]


# `/search` is declared before `/{tmdb_id}`: FastAPI matches routes in order, and the
# literal path has to win before the id pattern gets a look at it.


@router.get("/search")
async def search(
    query: Annotated[str, Query(min_length=1, max_length=SEARCH_QUERY_MAX)],
    account: CurrentAccount,
    db: DbSession,
    tmdb: AppTmdb,
) -> SearchResults:
    """Search TMDB, flagging every row the owner already tracks.

    Search deliberately does not fill the store: a page of results would cost a
    bundled call per row, and the owner is about to care about at most one of them.
    """
    hits = await catalog.search(tmdb, query)
    tracked = await _tracked(db, account, [hit.tmdb_id for hit in hits])
    return SearchResults(results=[SearchResult.of(hit, tracked.get(hit.tmdb_id)) for hit in hits])


@router.get("/{tmdb_id}")
async def film_page(
    tmdb_id: int, account: CurrentAccount, db: DbSession, tmdb: AppTmdb, settings: AppSettings
) -> FilmDetail:
    film = await catalog.ensure_film(db, tmdb, tmdb_id, settings.film_refresh_days)
    return FilmDetail.of(film, await _account_film(db, account, tmdb_id))


@router.post("/{tmdb_id}/backlog")
async def add_to_backlog(
    tmdb_id: int, account: CurrentAccount, db: DbSession, tmdb: AppTmdb, settings: AppSettings
) -> FilmDetail:
    """Put an untracked film in the backlog; adding one already there changes nothing."""
    film = await catalog.ensure_film(db, tmdb, tmdb_id, settings.film_refresh_days)
    account_film = await _account_film(db, account, tmdb_id)
    if account_film is None:
        account_film = AccountFilm(
            account_id=account.id, film_id=tmdb_id, state=LifecycleState.backlog
        )
        db.add(account_film)
        await db.commit()
    elif account_film.state is not LifecycleState.backlog:
        raise ApiError(409, "already_watched", "You have already watched this film.")
    return FilmDetail.of(film, account_film)


@router.delete("/{tmdb_id}/backlog", status_code=204)
async def remove_from_backlog(tmdb_id: int, account: CurrentAccount, db: DbSession) -> None:
    """Take a film back out of the backlog, leaving it untracked - and so, no record."""
    account_film = await _account_film(db, account, tmdb_id)
    if account_film is None:
        return
    if account_film.state is not LifecycleState.backlog:
        raise ApiError(409, "not_in_backlog", "That film is not in your backlog.")
    await db.execute(delete(AccountFilm).where(AccountFilm.id == account_film.id))
    await db.commit()


@router.post("/{tmdb_id}/watched")
async def mark_watched(
    tmdb_id: int,
    body: MarkWatched,
    account: CurrentAccount,
    db: DbSession,
    tmdb: AppTmdb,
    settings: AppSettings,
) -> FilmDetail:
    """Log a watch, and take the owner's answer to rate now or rate later.

    Either answer makes the film watched-unrated and appends a watch event. "Later" also
    seats the film in the rate-later queue; "now" leaves the seat to the placement flow,
    which takes it the moment the flow begins, so an abandoned attempt still lands the
    film in the queue.
    """
    film = await catalog.ensure_film(db, tmdb, tmdb_id, settings.film_refresh_days)
    account_film = await _account_film(db, account, tmdb_id)
    if account_film is None:
        account_film = AccountFilm(
            account_id=account.id, film_id=tmdb_id, state=LifecycleState.watched_unrated
        )
        db.add(account_film)
    elif account_film.state is LifecycleState.rated:
        # Marking a rated film watched is the rewatch flow, which arrives with #30.
        raise ApiError(409, "already_rated", "You have already rated this film.")
    account_film.state = LifecycleState.watched_unrated
    account_film.rate_later = body.rate == "later"
    db.add(_watch_event(account, tmdb_id))
    await db.commit()
    return FilmDetail.of(film, account_film)


def _watch_event(account: Account, tmdb_id: int) -> WatchEvent:
    """The watch, stamped with where the film stood and how it got there.

    Both stamps are capture-or-lose-forever (evaluation.md): tier membership churns and
    keeps no history, so nothing could reconstruct them later. Everything is plain
    backlog until the ranked tier exists (#33), and hand-added until discovery (#32) and
    the seed import (#29) can put a film in the owner's world any other way.
    """
    return WatchEvent(
        account_id=account.id,
        film_id=tmdb_id,
        standing=WatchStanding.plain_backlog,
        origin=WatchOrigin.hand_added,
    )


@router.delete("/{tmdb_id}/rate-later", status_code=204)
async def leave_rate_later(tmdb_id: int, account: CurrentAccount, db: DbSession) -> None:
    """Take a watched-unrated film out of the rate-later queue, still watched.

    The seat is removable at will and removing it never touches watched-ness: the owner
    is saying they do not intend to rate this one, not that they did not see it.
    """
    account_film = await _account_film(db, account, tmdb_id)
    if account_film is None or account_film.state is not LifecycleState.watched_unrated:
        raise ApiError(409, "not_watched_unrated", "That film is not waiting to be rated.")
    account_film.rate_later = False
    await db.commit()


# --- Helpers ---


async def _account_film(db: AsyncSession, account: Account, tmdb_id: int) -> AccountFilm | None:
    """This account's record for one film, or None where the film is untracked."""
    account_film: AccountFilm | None = await db.scalar(
        select(AccountFilm).where(
            AccountFilm.account_id == account.id, AccountFilm.film_id == tmdb_id
        )
    )
    return account_film


async def _tracked(
    db: AsyncSession, account: Account, tmdb_ids: list[int]
) -> dict[int, AccountFilm]:
    """This account's records for a page of films, in one query rather than one per row."""
    if not tmdb_ids:
        return {}
    rows = await db.scalars(
        select(AccountFilm).where(
            AccountFilm.account_id == account.id, AccountFilm.film_id.in_(tmdb_ids)
        )
    )
    return {row.film_id: row for row in rows}
