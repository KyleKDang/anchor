"""Background jobs on the Postgres-backed queue (procrastinate).

Each process builds its own queue app against its own connection pool; the web
process enqueues, the worker executes. The task functions are plain coroutines
registered on a fresh blueprint per app: procrastinate binds a Task to the last app
it was added to (and re-prefixes its name each time), so one blueprint cannot be
shared between apps.
"""

from collections.abc import Awaitable, Callable
from typing import Any

import procrastinate
from procrastinate import JobContext, builtin_tasks
from sqlalchemy import func, update
from sqlalchemy.ext.asyncio import AsyncSession

from anchor.db import Database
from anchor.models import WorkerProbe
from anchor.settings import Settings

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


def worker_context(db: Database) -> dict[str, Any]:
    """What every job receives as its ``additional_context``; read back with ``database_of``."""
    return {"db": db}


def database_of(context: JobContext) -> Database:
    return context.additional_context["db"]  # type: ignore[no-any-return]


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


def _declare_tasks() -> procrastinate.Blueprint:
    tasks = procrastinate.Blueprint()
    tasks.task(name=answer_probe.__name__, pass_context=True)(answer_probe)
    for nightly, cron in [(remove_old_jobs, "0 4 * * *")]:
        task = tasks.task(name=nightly.__name__, queueing_lock=nightly.__name__, pass_context=True)(
            nightly
        )
        tasks.periodic(cron=cron)(task)
    return tasks
