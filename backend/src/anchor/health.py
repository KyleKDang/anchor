"""The health check: web, database, and worker, proven by a job round trip."""

import asyncio
import logging
import uuid
from datetime import timedelta
from typing import Literal

import procrastinate
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from anchor import jobs
from anchor.db import Database
from anchor.models import WorkerProbe

router = APIRouter()
log = logging.getLogger(__name__)

PROBE_RETENTION = timedelta(days=1)
POLL_INTERVAL = 0.1

CheckStatus = Literal["ok", "error", "timeout", "skipped"]


@router.get("/api/health")
async def health(request: Request) -> JSONResponse:
    db: Database = request.app.state.db
    jobs_app = request.app.state.jobs
    timeout: float = request.app.state.settings.health_worker_timeout
    checks: dict[str, CheckStatus] = {"web": "ok"}

    try:
        probe_id = await _ask_worker(db, jobs_app)
    except Exception:
        log.exception("health: database check failed")
        checks["database"] = "error"
        checks["worker"] = "skipped"
    else:
        checks["database"] = "ok"
        checks["worker"] = "ok" if await _worker_answered(db, probe_id, timeout) else "timeout"

    healthy = all(check == "ok" for check in checks.values())
    return JSONResponse(
        {"status": "ok" if healthy else "degraded", "checks": checks},
        status_code=200 if healthy else 503,
    )


@router.get("/api/debug/error")
async def debug_error() -> None:
    """Fail on purpose: hitting this in production must produce a Sentry event."""
    raise RuntimeError("deliberate backend error to check Sentry")


async def _ask_worker(db: Database, jobs_app: procrastinate.App) -> uuid.UUID:
    """Record a probe and enqueue its answer in one transaction; return the probe id.

    The same transaction prunes probes past ``PROBE_RETENTION``, so the table stays
    bounded however often the check is polled.
    """
    async with db.sessions() as session:
        probe = WorkerProbe()
        session.add(probe)
        await session.flush()
        await jobs.enqueue(session, jobs_app, jobs.answer_probe, probe_id=str(probe.id))
        await _prune_old_probes(session)
        await session.commit()
        return probe.id


async def _prune_old_probes(session: AsyncSession) -> None:
    await session.execute(
        delete(WorkerProbe).where(WorkerProbe.requested_at < func.now() - PROBE_RETENTION)
    )


async def _worker_answered(db: Database, probe_id: uuid.UUID, timeout: float) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        async with db.sessions() as session:
            answered_at = await session.scalar(
                select(WorkerProbe.answered_at).where(WorkerProbe.id == probe_id)
            )
        if answered_at is not None:
            return True
        if asyncio.get_running_loop().time() >= deadline:
            return False
        await asyncio.sleep(POLL_INTERVAL)
