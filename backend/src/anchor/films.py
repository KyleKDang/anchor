"""Search, the film page, and the backlog transitions that run from either.

Every endpoint here is film-scoped. Search reads TMDB and flags the rows the owner
already knows; opening a film page or adding a film fills the shared store, which is
the only thing that spends a TMDB call per film.

A film's lifecycle state is exclusive and untracked films have no record at all, so
these transitions create the record on the way in and delete it on the way back out.
"""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from anchor import catalog, rewatch
from anchor import ordering as ordering_module
from anchor import tier as tier_module
from anchor.accounts import CurrentAccount
from anchor.catalog import FilmCard, FilmDetail, SearchResult
from anchor.deps import AppSettings, AppTmdb, DbSession
from anchor.errors import ApiError
from anchor.models import (
    Account,
    AccountFilm,
    ComparisonKind,
    ComparisonLogEntry,
    ComparisonVerdict,
    Film,
    LifecycleState,
    QualityListEntry,
    WatchEvent,
    WatchOrigin,
)
from anchor.rewatch import RewatchPrompt
from anchor.tmdb import Browse

router = APIRouter(prefix="/api/films")

SEARCH_QUERY_MAX = 200


class SearchResults(BaseModel):
    results: list[SearchResult]


class Judgment(BaseModel):
    """One of the film's own comparison-log entries, as the page shows it back.

    No status and no flag: an entry the ordering has since been moved past is shown
    exactly as it was made, and the reader compares it with the band and rank above it -
    the ordering wins (ADR 0013). What the page owes the owner is the record of what they
    said, not a verdict on whether they still mean it.
    """

    kind: ComparisonKind
    other: FilmCard | None
    """The film this judgment set the subject against; absent on a plain band pick."""
    verdict: ComparisonVerdict | None
    """Which film won a comparison. None on a band pick, whose answer is the band."""
    band: float | None
    """The band a pick chose. None on every comparison."""
    quality: str | None
    """The quality a criteria answer was about. None on every other kind."""
    created_at: datetime


class Neighbours(BaseModel):
    """The films immediately above and below a rated film, inside its own band.

    Band-local because the rank is: "third of your 4.0s" is a statement about the 4.0s,
    so the films it is against are the other 4.0s. An end of the row has no neighbour
    that way, and None is the honest answer rather than the next band's edge.

    Shared with the picker's done screen, which makes the same statement about the same
    film a moment earlier.
    """

    above: FilmCard | None
    below: FilmCard | None


class FilmPage(FilmDetail):
    """The film page: a film's standing, plus what only a rated film carries.

    Every rated-only field is absent on films in any other state, which is stronger than
    carrying it empty: an unwatched film has no rank to have and no rewatch to answer,
    and ADR 0005 wants that said by absence.
    """

    rank: int | None = None
    """Where the film sits inside its band, 1 the best."""
    band_size: int | None = None
    """How many films the band holds, so the rank reads as "3 of 41"."""
    neighbours: Neighbours | None = None
    rewatch: RewatchPrompt | None = None
    """The still-feel-the-same question the last rewatch left open."""
    judgments: list[Judgment] = []
    """The film's comparison-log entries, newest first."""


class MarkWatched(BaseModel):
    """Logging a watch is always a choice between rating it now and rating it later.

    Except on a film that is already rated, where there is nothing to rate later and the
    same button means a rewatch: the answer is the still-feel-the-same question instead,
    so the field is left off rather than given a meaning it does not have there.
    """

    rate: Literal["now", "later"] | None = None


# `/search` and `/browse` are declared before `/{tmdb_id}`: FastAPI matches routes in
# order, and the literal paths have to win before the id pattern gets a look at them.


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
    derived = await ordering_module.bands_of(db, account.id)
    return SearchResults(
        results=[
            SearchResult.of(hit, tracked.get(hit.tmdb_id), derived.get(hit.tmdb_id)) for hit in hits
        ]
    )


@router.get("/browse")
async def browse(
    kind: Browse,
    account: CurrentAccount,
    db: DbSession,
    tmdb: AppTmdb,
) -> SearchResults:
    """TMDB's popular and top-rated grids: the warmup's "need inspiration?" fallback.

    Explicitly a fallback and never the headline act. A popularity grid biases hard
    toward blockbusters, so an owner led with one designates the films everybody has
    seen rather than the films they themselves know cold (onboarding-and-import.md).

    Flagged and store-free exactly as search is, for the same reason: the owner is
    about to care about at most one of these rows.
    """
    hits = await catalog.browse(tmdb, kind)
    tracked = await _tracked(db, account, [hit.tmdb_id for hit in hits])
    derived = await ordering_module.bands_of(db, account.id)
    return SearchResults(
        results=[
            SearchResult.of(hit, tracked.get(hit.tmdb_id), derived.get(hit.tmdb_id)) for hit in hits
        ]
    )


@router.get("/{tmdb_id}")
async def film_page(
    tmdb_id: int, account: CurrentAccount, db: DbSession, tmdb: AppTmdb, settings: AppSettings
) -> FilmPage:
    film = await catalog.ensure_film(db, tmdb, tmdb_id, settings.film_refresh_days)
    return await _detail(db, account, film, await _account_film(db, account, tmdb_id))


@router.post("/{tmdb_id}/backlog")
async def add_to_backlog(
    tmdb_id: int, account: CurrentAccount, db: DbSession, tmdb: AppTmdb, settings: AppSettings
) -> FilmPage:
    """Put an untracked film in the backlog; adding one already there changes nothing."""
    film = await catalog.ensure_film(db, tmdb, tmdb_id, settings.film_refresh_days)
    account_film = await _account_film(db, account, tmdb_id)
    if account_film is None:
        account_film = AccountFilm(
            account_id=account.id, film_id=tmdb_id, state=LifecycleState.backlog
        )
        db.add(account_film)
        await db.flush()
        # The newly-backlogged exception (watchlist.md): a film the owner just added takes
        # a seat the moment it scores in, rather than waiting behind the swap budget. The
        # owner told the app something, and reacting to it is the point.
        await tier_module.reconcile(db, account.id, settings, admit=tmdb_id)
        await db.commit()
    elif account_film.state is not LifecycleState.backlog:
        raise ApiError(409, "already_watched", "You have already watched this film.")
    return await _detail(db, account, film, account_film)


@router.delete("/{tmdb_id}/backlog", status_code=204)
async def remove_from_backlog(
    tmdb_id: int, account: CurrentAccount, db: DbSession, settings: AppSettings
) -> None:
    """Take a film back out of the backlog, leaving it untracked - and so, no record."""
    account_film = await _account_film(db, account, tmdb_id)
    if account_film is None:
        return
    if account_film.state is not LifecycleState.backlog:
        raise ApiError(409, "not_in_backlog", "That film is not in your backlog.")
    await db.execute(delete(AccountFilm).where(AccountFilm.id == account_film.id))
    await db.flush()
    await tier_module.reconcile(db, account.id, settings)
    await db.commit()


@router.post("/{tmdb_id}/watched")
async def mark_watched(
    tmdb_id: int,
    body: MarkWatched,
    account: CurrentAccount,
    db: DbSession,
    tmdb: AppTmdb,
    settings: AppSettings,
) -> FilmPage:
    """Log a watch, and take the owner's answer to rate now or rate later.

    On a film already rated this is the rewatch instead, and nothing below applies: the
    film keeps its state, its slot, and its rating, because watching something twice is
    not a judgment about it.

    Otherwise: either answer makes the film watched-unrated, appends a watch event, and seats the
    film in the rate-later queue. The seat is not the "later" branch's doing: it is the
    resting state of any watched-unrated film, and taking it here is what makes walking
    away safe at every point without the client having to signal that it happened.
    Landing a placement clears it; so does the owner saying they will not rate this one.
    """
    film = await catalog.ensure_film(db, tmdb, tmdb_id, settings.film_refresh_days)
    account_film = await _account_film(db, account, tmdb_id)
    if account_film is not None and account_film.state is LifecycleState.rated:
        # Marking a rated film watched is the rewatch flow: the film stays rated, the
        # watch is appended, and the owner is offered one light question about it.
        await rewatch.log(db, account.id, account_film)
        await db.commit()
        return await _detail(db, account, film, account_film)
    if account_film is None:
        account_film = AccountFilm(
            account_id=account.id, film_id=tmdb_id, state=LifecycleState.watched_unrated
        )
        db.add(account_film)
    # Read before the state moves: the standing stamp is capture-or-lose-forever, and
    # a watched film's seat is cleared by the very next line of maintenance.
    db.add(_watch_event(account, account_film))
    account_film.state = LifecycleState.watched_unrated
    account_film.rate_later = True
    await db.flush()
    # A seat the owner just emptied by watching what was in it: refilling it is not churn,
    # so it happens now rather than at the next session boundary (watchlist.md).
    await tier_module.reconcile(db, account.id, settings)
    await db.commit()
    return await _detail(db, account, film, account_film)


def _watch_event(account: Account, account_film: AccountFilm) -> WatchEvent:
    """The watch, stamped with where the film stood and how it got there.

    Both stamps are capture-or-lose-forever (evaluation.md): tier membership churns and
    keeps no history, so nothing could reconstruct them later. The origin is hand-added
    until discovery can put a film in the owner's world another way.
    """
    return WatchEvent(
        account_id=account.id,
        film_id=account_film.film_id,
        standing=tier_module.standing(account_film),
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


async def _detail(
    db: AsyncSession, account: Account, film: Film, account_film: AccountFilm | None
) -> FilmPage:
    """The film page: the film's standing, and its whole rated context where it has one.

    Nothing rating-shaped is computed for an unwatched film (ADR 0005): everything below
    the state check is read off a placement, and a film without one has none of it.
    """
    if account_film is None or account_film.state is not LifecycleState.rated:
        return FilmPage.of(film, account_film)
    ordering = await ordering_module.load(db, account.id)
    standing = ordering.standing(film.tmdb_id)
    assert standing is not None  # a rated film is a placed film
    cards = await ordering_module.cards(db, standing.named())
    page = FilmPage.of(film, account_film, standing.band, anchor=standing.anchored)
    page.rank = standing.rank
    page.band_size = standing.band_size
    page.neighbours = Neighbours(
        above=cards.get(standing.above) if standing.above else None,
        below=cards.get(standing.below) if standing.below else None,
    )
    page.rewatch = await rewatch.prompt(db, account.id, film.tmdb_id)
    page.judgments = await _judgments(db, account.id, film.tmdb_id)
    return page


async def _judgments(db: AsyncSession, account_id: uuid.UUID, tmdb_id: int) -> list[Judgment]:
    """This film's own log entries, newest first, read against the ordering as it stands."""
    rows = list(
        await db.execute(
            select(ComparisonLogEntry, QualityListEntry.name)
            .outerjoin(QualityListEntry, QualityListEntry.id == ComparisonLogEntry.quality_id)
            .where(
                ComparisonLogEntry.account_id == account_id,
                ComparisonLogEntry.subject_film_id == tmdb_id,
            )
            .order_by(ComparisonLogEntry.created_at.desc(), ComparisonLogEntry.id)
        )
    )
    others = await ordering_module.cards(
        db,
        [other for entry, _ in rows if (other := _other_film(entry, tmdb_id)) is not None],
    )
    return [
        Judgment(
            kind=entry.kind,
            other=others.get(other) if (other := _other_film(entry, tmdb_id)) else None,
            verdict=entry.verdict,
            band=entry.band,
            quality=quality,
            created_at=entry.created_at,
        )
        for entry, quality in rows
    ]


def _other_film(entry: ComparisonLogEntry, tmdb_id: int) -> int | None:
    """The film on the other side of a judgment, or None where it involved one film.

    A criteria answer is about a pair neither of which need be the subject's own side of
    the row, so both columns are checked rather than ``film_b_id`` assumed.
    """
    if entry.film_b_id is None:
        return None
    return entry.film_b_id if entry.film_a_id == tmdb_id else entry.film_a_id


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
