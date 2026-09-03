"""Background jobs on the Postgres-backed queue (procrastinate).

Each process builds its own queue app against its own connection pool; the web
process enqueues, the worker executes. The task functions are plain coroutines
registered on a fresh blueprint per app: procrastinate binds a Task to the last app
it was added to (and re-prefixes its name each time), so one blueprint cannot be
shared between apps.
"""

import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import procrastinate
from procrastinate import JobContext, builtin_tasks
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from anchor import catalog, matching, seeding
from anchor.db import Database
from anchor.errors import ApiError
from anchor.models import AuthSession, Import, ImportRow, ImportRowState, ImportStatus, WorkerProbe
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
    session: AsyncSession,
    jobs: procrastinate.App,
    task: TaskFunction,
    *,
    lock: str | None = None,
    **kwargs: Any,
) -> int:
    """Enqueue ``task`` in the session's open transaction.

    The job row commits or rolls back together with the session's data changes,
    so a data change can never be persisted with its follow-up job lost.

    Jobs sharing a ``lock`` run one at a time, in the order they were enqueued.
    """
    connection = await session.connection()
    raw = await connection.get_raw_connection()
    deferrer = jobs.configure_task(
        name=task_name(task), connection=raw.driver_connection, lock=lock
    )
    return await deferrer.defer_async(**kwargs)


async def schedule_retrain(
    session: AsyncSession, jobs: procrastinate.App, account_id: uuid.UUID
) -> None:
    """Queue this account's retrain alongside the change that made it necessary.

    Called from every flow that moves the ordering, a divider, or a designation, and
    inside that flow's own transaction: a placement that lands with its retrain lost
    would leave the taste profile quietly describing an ordering that no longer exists.
    The account lock keeps two retrains from regenerating the same artifacts at once.
    """
    await enqueue(
        session, jobs, retrain_taste_profile, lock=str(account_id), account_id=str(account_id)
    )


async def answer_probe(context: JobContext, probe_id: str) -> None:
    """The health check's round trip: mark the probe answered."""
    async with database_of(context).sessions() as session:
        await session.execute(
            update(WorkerProbe).where(WorkerProbe.id == probe_id).values(answered_at=func.now())
        )
        await session.commit()


async def retrain_taste_profile(context: JobContext, account_id: str) -> None:
    """Regenerate the account's weight vector and exemplar set, and record the retrain.

    Off the request path deliberately: the owner is answering the next comparison while
    this runs, and nothing they can see is waiting on it.

    The trainer is imported here rather than at the top of the module, so that only the
    worker ever loads it. The web process imports this module to *enqueue*, and pulling
    numpy and the whole feature pipeline into it for that would be a structural claim
    nobody meant to make - the same rule architecture.md puts on the LLM module.
    """
    from anchor import taste

    async with database_of(context).sessions() as session:
        await taste.retrain(session, uuid.UUID(account_id))
        await session.commit()


async def match_import_rows(context: JobContext, import_id: str) -> None:
    """Work an import's rows: apply what the matcher is sure of, queue the rest to review.

    Off the request path because six hundred rows is six hundred TMDB searches, and the
    owner is meant to be using the app while it runs. Each row commits on its own, so
    the library fills in front of them rather than appearing all at once - and so a
    retry after TMDB goes down resumes at the row it stopped on rather than starting the
    export again.
    """
    db, tmdb = database_of(context), tmdb_of(context)
    settings = settings_of(context)
    async with db.sessions() as session:
        record = await session.get(Import, uuid.UUID(import_id))
        if record is None:
            return  # the import was wiped by a re-import while this waited its turn
        account_id = record.account_id
        pending = list(
            await session.scalars(
                select(ImportRow.id)
                .where(ImportRow.import_id == record.id, ImportRow.state == ImportRowState.pending)
                # Not by created_at: the whole export is inserted in one transaction, so
                # every row carries the same stamp and the tiebreak would be a random
                # uuid, giving a different order every run. Kind first is the useful
                # order anyway - ratings landing before the watchlist and watched rows is
                # what lets those skip a film the owner has already rated.
                .order_by(ImportRow.kind, ImportRow.name, ImportRow.id)
            )
        )

    for row_id in pending:
        async with db.sessions() as session:
            row = await session.get(ImportRow, row_id)
            if row is None or row.state is not ImportRowState.pending:
                continue
            await _match_row(session, tmdb, settings, account_id, row)
            await session.commit()

    async with db.sessions() as session:
        record = await session.get(Import, uuid.UUID(import_id))
        if record is None:
            return  # wiped mid-run: nothing is left to complete, or to retrain over
        record.status = ImportStatus.complete
        await session.commit()

    # The trainer is called rather than deferred: this is already the worker, and one
    # retrain at the end beats six hundred queued behind the same account lock. It runs
    # after the completion has committed, in a transaction of its own, because the taste
    # profile is a derived artifact and nothing about the import's outcome depends on it.
    # Sharing the transaction meant a retrain the kernel killed took the status flip down
    # with it, and an import that reads ``matching`` forever hides the review queue, the
    # unmatched list and the re-import control: a finished account, wedged.
    from anchor import taste

    async with db.sessions() as session:
        try:
            await taste.retrain(session, account_id)
            await session.commit()
        except Exception:
            # Logged rather than failing the job. The job's retry exists for TMDB going
            # down mid-row, and re-running a finished import only reaches the same
            # failing call. The profile stays stale until the next retrain, and every
            # change to the ordering schedules one - starting with the review queue the
            # owner works through next.
            log.exception("retrain after import %s failed; the taste profile is stale", import_id)


async def _match_row(
    session: AsyncSession,
    tmdb: Tmdb,
    settings: Settings,
    account_id: uuid.UUID,
    row: ImportRow,
) -> None:
    """One row's whole fate. A film TMDB has dropped since is unmatched, not a failure."""
    try:
        found = await matching.match(tmdb, settings, row.name, row.year)
        if found.accepted is not None:
            await seeding.apply(session, account_id, row, found.accepted, tmdb, settings)
            row.state = ImportRowState.auto_matched
            return
    except ApiError as error:
        if error.status_code != 404:
            raise  # TMDB is down: leave the rest pending and let the retry resume
        found = matching.Match()
    if found.candidates:
        row.candidates = list(found.candidates)
        row.state = ImportRowState.review_pending
    else:
        row.state = ImportRowState.unmatched_open


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
    tasks.task(name=retrain_taste_profile.__name__, pass_context=True)(retrain_taste_profile)
    # Retried, because the whole job is one long conversation with TMDB and the far end
    # goes down. Every row commits on its own, so a retry resumes rather than repeats.
    tasks.task(name=match_import_rows.__name__, retry=3, pass_context=True)(match_import_rows)
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
