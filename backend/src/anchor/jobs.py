"""Background jobs on the Postgres-backed queue (procrastinate).

Tasks are declared on a blueprint so that each process builds its own queue app
against its own connection pool; the web process enqueues, the worker executes.
"""

from typing import Any

import procrastinate
from procrastinate import JobContext, builtin_tasks
from procrastinate.tasks import Task
from sqlalchemy import func, update
from sqlalchemy.ext.asyncio import AsyncSession

from anchor.db import Database
from anchor.models import WorkerProbe
from anchor.settings import Settings

tasks = procrastinate.Blueprint()


def build_app(settings: Settings) -> procrastinate.App:
    connector = procrastinate.PsycopgConnector(conninfo=settings.database_url)
    app = procrastinate.App(connector=connector)
    app.add_tasks_from(tasks, namespace="anchor")
    return app


def worker_context(db: Database) -> dict[str, Any]:
    """What every job receives as its ``additional_context``; read back with ``database_of``."""
    return {"db": db}


def database_of(context: JobContext) -> Database:
    return context.additional_context["db"]  # type: ignore[no-any-return]


async def enqueue(
    session: AsyncSession, jobs: procrastinate.App, task: Task[Any, Any, Any], **kwargs: Any
) -> int:
    """Enqueue ``task`` in the session's open transaction.

    The job row commits or rolls back together with the session's data changes,
    so a data change can never be persisted with its follow-up job lost.
    """
    connection = await session.connection()
    raw = await connection.get_raw_connection()
    return await jobs.configure_task(name=task.name, connection=raw.driver_connection).defer_async(
        **kwargs
    )


@tasks.task(pass_context=True)
async def answer_probe(context: JobContext, probe_id: str) -> None:
    """The health check's round trip: mark the probe answered."""
    async with database_of(context).sessions() as session:
        await session.execute(
            update(WorkerProbe).where(WorkerProbe.id == probe_id).values(answered_at=func.now())
        )
        await session.commit()


@tasks.periodic(cron="0 4 * * *")
@tasks.task(queueing_lock="remove_old_jobs", pass_context=True)
async def remove_old_jobs(context: JobContext, timestamp: int) -> None:
    """Nightly hygiene: drop finished job rows (health probes alone add one per check)."""
    await builtin_tasks.remove_old_jobs(context, max_hours=24)
