"""The Watchlist screen: the engine's ranked tier, and the backlog it is drawn from.

One screen, two tiers. Below taste-profile readiness *ready* there is only the backlog,
honestly unranked, with an explainer and an ambient line saying how far off the unlock
is: a fake popularity-ranked tier would teach the owner on day one that the tier's
opinion is worthless (onboarding-and-import.md).

The backlog's sorts are recently-added, title, and year - and deliberately not engine
score. ADR 0005 bars anything rating-shaped on unwatched films, and a score-ordered
backlog would quietly become a second, undamped ranked tier. The sort parameter is a
closed set, so asking for a score sort is refused rather than silently ignored.

The two halves never list the same film twice: a film holding a tier seat is listed in
the tier and left out of the backlog below it, which is the same set the spec describes
read as one screen rather than as two overlapping queries. A vetoed film is a backlog
film, and the tier's vetoed list is not a second listing of it but the place its undo
lives, so the owner can review what they barred without hunting the backlog for it.

Reading the tier is also the moment the tier is maintained, and the moment the one-time
unlock dot is cleared. Both are deliberate: a session boundary is a moment rather than a
record (data-model.md), and the maintenance a read triggers is gated on the fingerprint
of its own inputs, so the list a second read returns is the list the first one did.
"""

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import ColumnElement, and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import UnaryExpression

from anchor import readiness as readiness_module
from anchor import tier as tier_module
from anchor.accounts import CurrentAccount
from anchor.catalog import BacklogFilm
from anchor.deps import AppSettings, DbSession
from anchor.errors import ApiError
from anchor.models import Account, AccountFilm, Film, LifecycleState, TierZone
from anchor.profile import Threshold
from anchor.readiness import Readiness
from anchor.settings import Settings

router = APIRouter(prefix="/api/watchlist")
unlocks = APIRouter(prefix="/api/unlocks")
"""The nav's own read. Separate because the dot has to show from any screen, and because
surfacing.md has exactly two of them - Discovery's arrives with its own ticket."""

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
        .where(_below_the_tier(account.id), *_filters(genre, decade))
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


def _below_the_tier(account_id: uuid.UUID) -> ColumnElement[bool]:
    """The backlog as the screen lists it: every backlog film not holding a tier seat."""
    return and_(_in_backlog(account_id), AccountFilm.tier_zone.is_(None))


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
        .where(_below_the_tier(account_id))
    )
    return sorted({genre for genres in rows for genre in genres})


async def _available_decades(db: AsyncSession, account_id: uuid.UUID) -> list[int]:
    rows = await db.scalars(
        select(Film.release_year)
        .join(AccountFilm, AccountFilm.film_id == Film.tmdb_id)
        .where(_below_the_tier(account_id), Film.release_year.is_not(None))
    )
    return sorted({year - year % DECADE_SPAN for year in rows if year is not None}, reverse=True)


# --- The ranked tier ---


class TierFilm(BacklogFilm):
    """A tier row. Unwatched like every backlog row, so nothing rating-shaped exists.

    Position is the whole of the engine's statement and it is carried by the order of the
    list, not by a number on the row: a rank printed beside a film is one short step from
    a score printed beside it, and ADR 0005 rules out the second.
    """

    pinned: bool
    """The owner put this here, so the row offers to take it back rather than to pin it."""

    @classmethod
    def seated(cls, film: Film, account_film: AccountFilm) -> "TierFilm":
        return cls(
            **BacklogFilm.of(film, account_film).model_dump(),
            pinned=account_film.pinned_at is not None,
        )


class Progress(BaseModel):
    """How close the account is to unlocking the tier: one line and one subtle bar.

    Ambient only, and the loudness ceiling for the pre-gate screen (surfacing.md). The
    thresholds are the engine's own bars, so the screen cannot promise a number the
    engine is not gating on; ``share`` is those bars averaged, which is the bar to draw.
    """

    share: float
    thresholds: list[Threshold]


class Tier(BaseModel):
    """The Watchlist screen's top half, and what stands in for it before the unlock."""

    readiness: Readiness
    unlocked: bool
    """The tier exists. Below ready both lists are empty and ``progress`` says why."""
    progress: Progress | None
    up_next: list[TierFilm]
    """Strictly ordered: a real "watch these next" statement, pins first."""
    pool: list[TierFilm]
    """The rest of the top thirty, loosely ordered - the order floats freely."""
    vetoed: list[BacklogFilm]
    """Barred from the tier until lifted, and never presented as distaste."""


class Unlocks(BaseModel):
    """The nav's dots. One per readiness unlock, and nothing else ever gets one."""

    watchlist: bool


@router.get("/tier")
async def tier(
    account: CurrentAccount, db: DbSession, settings: AppSettings, boundary: bool = True
) -> Tier:
    """The ranked tier as it now stands - which is also the moment it is maintained.

    A session boundary is the owner arriving at the screen, and that is the only read
    that maintains the tier. The screen reloading after the owner's own action - a watch,
    a pin, a veto - says so with ``boundary=false``, and gets back what the action did and
    nothing else: the engine's own swaps and rotations wait for the next arrival, so the
    list the owner is acting on never moves under their cursor (watchlist.md). The one
    exception is the first visit after the unlock, which is an arrival whatever the
    client calls it - the dot it clears was pointing at a tier that has to be there.

    The maintenance is also a no-op unless the fit or the watch clock has moved since the
    last one, so an arrival in a second tab spends no second swap budget.
    """
    await tier_module.note_unlock(db, account.id, settings)
    if boundary or await tier_module.pending_unlock(db, account.id):
        await tier_module.refresh(db, account.id, settings)
    await tier_module.clear_unlock(db, account.id)
    await db.commit()
    return await _tier(db, account, settings)


@router.post("/{tmdb_id}/pin", status_code=204)
async def pin(tmdb_id: int, account: CurrentAccount, db: DbSession, settings: AppSettings) -> None:
    """Hold this film at the top until it is watched, unpinned, or dropped from the backlog."""
    account_film = await _overridable(db, account, tmdb_id, settings)
    if account_film.pinned_at is None and await _pins(db, account.id) >= tier_module.UP_NEXT:
        raise ApiError(409, "pin_cap", f"You can pin at most {tier_module.UP_NEXT} films.")
    await tier_module.pin(db, account_film, settings)
    await db.commit()


@router.delete("/{tmdb_id}/pin", status_code=204)
async def unpin(
    tmdb_id: int, account: CurrentAccount, db: DbSession, settings: AppSettings
) -> None:
    """Give the seat back to the engine, which may keep the film in it on its own merits."""
    await tier_module.unpin(db, await _overridable(db, account, tmdb_id, settings), settings)
    await db.commit()


@router.post("/{tmdb_id}/veto", status_code=204)
async def veto(tmdb_id: int, account: CurrentAccount, db: DbSession, settings: AppSettings) -> None:
    """Keep this out of the queue. Not distaste: the film keeps its place in the backlog."""
    await tier_module.veto(db, await _overridable(db, account, tmdb_id, settings), settings)
    await db.commit()


@router.delete("/{tmdb_id}/veto", status_code=204)
async def lift(tmdb_id: int, account: CurrentAccount, db: DbSession, settings: AppSettings) -> None:
    """Put it back in the running, at exactly the standing it would have had."""
    await tier_module.lift(db, await _overridable(db, account, tmdb_id, settings), settings)
    await db.commit()


@router.post("/{tmdb_id}/not-now", status_code=204)
async def not_now(
    tmdb_id: int, account: CurrentAccount, db: DbSession, settings: AppSettings
) -> None:
    """Rotate it out for a while. The mood-level version of a veto, and just as reversible."""
    await tier_module.not_now(db, await _overridable(db, account, tmdb_id, settings), settings)
    await db.commit()


@unlocks.get("")
async def unlock_dots(account: CurrentAccount, db: DbSession, settings: AppSettings) -> Unlocks:
    """Whether the nav is showing a dot. Read from every screen, so it arms the dot too."""
    await tier_module.note_unlock(db, account.id, settings)
    await db.commit()
    return Unlocks(watchlist=await tier_module.pending_unlock(db, account.id))


# --- Reading the tier ---


async def _tier(db: AsyncSession, account: Account, settings: Settings) -> Tier:
    """The persisted tier, read back verbatim: nothing here decides anything."""
    counted = await readiness_module.evidence(db, account.id)
    state = readiness_module.classify(counted, settings)
    if state is not Readiness.ready:
        return Tier(
            readiness=state,
            unlocked=False,
            progress=_progress(counted, settings),
            up_next=[],
            pool=[],
            vetoed=[],
        )
    rows = list(
        await db.execute(
            select(Film, AccountFilm)
            .join(AccountFilm, AccountFilm.film_id == Film.tmdb_id)
            .where(_in_backlog(account.id), AccountFilm.tier_zone.is_not(None))
            .order_by(AccountFilm.tier_position)
        )
    )
    return Tier(
        readiness=state,
        unlocked=True,
        progress=None,
        up_next=[TierFilm.seated(*row) for row in rows if row[1].tier_zone is TierZone.up_next],
        pool=[TierFilm.seated(*row) for row in rows if row[1].tier_zone is TierZone.pool],
        vetoed=await _vetoed(db, account.id),
    )


def _progress(evidence: readiness_module.Evidence, settings: Settings) -> Progress:
    """How far along the unlock is. The ready bars, and their average as one number."""
    bars = readiness_module.bars(evidence, settings)[Readiness.ready]
    shares = [min(bar.have / bar.need, 1.0) if bar.need else 1.0 for bar in bars]
    return Progress(
        share=sum(shares) / len(shares) if shares else 0.0,
        thresholds=[Threshold.of(bar) for bar in bars],
    )


async def _vetoed(db: AsyncSession, account_id: uuid.UUID) -> list[BacklogFilm]:
    """The vetoed list behind the screen's overflow: reviewable, and liftable one by one."""
    rows = await db.execute(
        select(Film, AccountFilm)
        .join(AccountFilm, AccountFilm.film_id == Film.tmdb_id)
        .where(_in_backlog(account_id), AccountFilm.vetoed_at.is_not(None))
        .order_by(AccountFilm.vetoed_at.desc(), Film.title)
    )
    return [BacklogFilm.of(film, account_film) for film, account_film in rows]


async def _pins(db: AsyncSession, account_id: uuid.UUID) -> int:
    return (
        await db.scalar(
            select(func.count())
            .select_from(AccountFilm)
            .where(_in_backlog(account_id), AccountFilm.pinned_at.is_not(None))
        )
    ) or 0


async def _overridable(
    db: AsyncSession, account: Account, tmdb_id: int, settings: Settings
) -> AccountFilm:
    """The backlog film an override is about, refused where the tier does not exist yet.

    Below ready there is no queue to manage: pin, veto, and not-now are all statements
    about where a film sits in the engine's list, and the honest answer before the unlock
    is that there is no list (onboarding-and-import.md).
    """
    if await readiness_module.state(db, account.id, settings) is not Readiness.ready:
        raise ApiError(409, "tier_locked", "Your ranked tier is not unlocked yet.")
    account_film = await db.scalar(
        select(AccountFilm).where(
            AccountFilm.account_id == account.id, AccountFilm.film_id == tmdb_id
        )
    )
    if account_film is None or account_film.state is not LifecycleState.backlog:
        raise ApiError(404, "not_in_backlog", "That film is not in your backlog.")
    return account_film
