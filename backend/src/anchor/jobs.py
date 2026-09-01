"""Background jobs on the Postgres-backed queue (procrastinate).

Each process builds its own queue app against its own connection pool; the web
process enqueues, the worker executes. The task functions are plain coroutines
registered on a fresh blueprint per app: procrastinate binds a Task to the last app
it was added to (and re-prefixes its name each time), so one blueprint cannot be
shared between apps.
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

import procrastinate
from procrastinate import JobContext, builtin_tasks
from sqlalchemy import delete, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from anchor import catalog
from anchor.db import Database
from anchor.models import AuthSession, WorkerProbe
from anchor.settings import Settings
from anchor.tmdb import FilmNotInTmdb, Tmdb, TmdbUnavailable

log = logging.getLogger(__name__)

NAMESPACE = "anchor"

TaskFunction = Callable[..., Awaitable[None]]


def build_app(settings: Settings) -> procrastinate.App:
    connector = procrastinate.PsycopgConnector(conninfo=settings.database_url)
    app = procrastinate.App(connector=connector)
    app.add_tasks_from(_declare_tasks(), namespace=NAMESPACE)
    return app


def task_name(task: TaskFunction) -> str:
    """The queue's name for a task function declared below."""
    return f"{NAMESPACE}:{task.__name__}"


def worker_context(db: Database, tmdb: Tmdb, settings: Settings) -> dict[str, Any]:
    """What every job receives as its ``additional_context``; read back with the getters below."""
    return {"db": db, "tmdb": tmdb, "settings": settings}


def database_of(context: JobContext) -> Database:
    return context.additional_context["db"]  # type: ignore[no-any-return]


def tmdb_of(context: JobContext) -> Tmdb:
    return context.additional_context["tmdb"]  # type: ignore[no-any-return]


def settings_of(context: JobContext) -> Settings:
    return context.additional_context["settings"]  # type: ignore[no-any-return]


async def enqueue(
    session: AsyncSession, jobs: procrastinate.App, task: TaskFunction, **kwargs: Any
) -> int:
    """Enqueue ``task`` in the session's open transaction.

    The job row commits or rolls back together with the session's data changes,
    so a data change can never be persisted with its follow-up job lost.
    """
    connection = await session.connection()
    raw = await connection.get_raw_connection()
    deferrer = jobs.configure_task(name=task_name(task), connection=raw.driver_connection)
    return await deferrer.defer_async(**kwargs)


async def answer_probe(context: JobContext, probe_id: str) -> None:
    """The health check's round trip: mark the probe answered."""
    async with database_of(context).sessions() as session:
        await session.execute(
            update(WorkerProbe).where(WorkerProbe.id == probe_id).values(answered_at=func.now())
        )
        await session.commit()


async def remove_old_jobs(context: JobContext, timestamp: int) -> None:
    """Nightly hygiene: drop finished job rows (health probes alone add one per check)."""
    await builtin_tasks.remove_old_jobs(context, max_hours=24)


async def prune_expired_sessions(context: JobContext, timestamp: int) -> None:
    """Nightly hygiene: an expired login session is already refused; drop its row."""
    async with database_of(context).sessions() as session:
        await session.execute(delete(AuthSession).where(AuthSession.expires_at <= func.now()))
        await session.commit()


async def resync_stale_films(context: JobContext, timestamp: int) -> None:
    """The rolling re-sync: refresh still-referenced films before the cache ceiling.

    ADR 0003 caps how long TMDB data may be held at six months, so films are
    refreshed at roughly five. Only films some account still tracks are worth a call:
    an unreferenced row is nobody's, and re-fetches by itself on next use.
    """
    db, tmdb = database_of(context), tmdb_of(context)
    refresh_days = settings_of(context).film_refresh_days
    async with db.sessions() as session:
        stale = await catalog.stale_referenced_films(session, refresh_days)

    for done, tmdb_id in enumerate(stale):
        try:
            bundle = await tmdb.film(tmdb_id)
        except FilmNotInTmdb:
            # Pulled from TMDB since. The stored row is all the catalog has of the
            # film, and dropping it would break every account tracking it.
            log.warning("TMDB no longer has film %s; keeping the stored row", tmdb_id)
            continue
        except TmdbUnavailable:
            # Down or still throttling after its retries: stop rather than hammer it,
            # and let tomorrow's run pick up where this one left off.
            log.warning("TMDB unavailable; %s films left to re-sync", len(stale) - done)
            return
        async with db.sessions() as session:
            await catalog.store(session, bundle)


def _declare_tasks() -> procrastinate.Blueprint:
    tasks = procrastinate.Blueprint()
    tasks.task(name=answer_probe.__name__, pass_context=True)(answer_probe)
    nightly_tasks = [
        (remove_old_jobs, "0 4 * * *"),
        (prune_expired_sessions, "10 4 * * *"),
        (resync_stale_films, "30 4 * * *"),
    ]
    for nightly, cron in nightly_tasks:
        task = tasks.task(name=nightly.__name__, queueing_lock=nightly.__name__, pass_context=True)(
            nightly
        )
        tasks.periodic(cron=cron)(task)
    return tasks
