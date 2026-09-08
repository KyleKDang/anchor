"""The health check: web, database, and the worker, proven by the worker's own heartbeat.

Three questions live here and only one of them can make the stack unhealthy. *Is the
worker alive?* is the gate: the container healthcheck and `docker compose up --wait` read
it, so a false negative fails a deploy. *Is the worker keeping up?* is reported beside it
and never gates anything, because a queue with work in it is a working queue. *Does this
box have an LLM credential?* is reported the same way and gates nothing either: dev and CI
run keyless on purpose, and a keyless box is degraded rather than broken (#109).

Liveness is a read, never a round trip. The check used to enqueue a probe job and wait for
it to come back, which proved rather more than liveness: the probe joined the same single
queue as everything else, so an owner importing their library held it there past any
timeout worth setting and the check called a busy worker a dead one (#82). Procrastinate
already registers every worker and beats a heartbeat for it - ``reclaim_stalled_jobs``
reads the same signal - and a beat cannot queue behind anything.
"""

import logging
from typing import Literal, TypedDict

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from anchor import llm
from anchor.db import Database
from anchor.ratelimit import limited
from anchor.settings import Settings

router = APIRouter()
log = logging.getLogger(__name__)

CheckStatus = Literal["ok", "error", "down", "skipped"]

LlmCredential = Literal["configured", "missing"]
"""Whether this box could reach an LLM provider at all.

Whether, and nothing else: this endpoint is unauthenticated, and the only question an
owner needs answered from outside is *is anything there*.
"""

_BEATING_WORKERS = text(
    """
    SELECT count(*)
      FROM procrastinate_workers
     WHERE last_heartbeat > now() - make_interval(secs => :seconds)
    """
)

_BACKLOG = text(
    """
    SELECT count(*) AS waiting,
           extract(epoch FROM now() - min(coalesce(job.scheduled_at, deferred.at))) AS oldest
      FROM procrastinate_jobs job
      LEFT JOIN procrastinate_events deferred
             ON deferred.job_id = job.id AND deferred.type = 'deferred'
     WHERE job.status = 'todo'
       AND (job.scheduled_at IS NULL OR job.scheduled_at <= now())
    """
)


class Backlog(TypedDict):
    """What is waiting to run, and how long the oldest of it has been waiting."""

    waiting: int
    oldest_wait_seconds: float | None


@router.get("/api/health")
async def health(request: Request) -> JSONResponse:
    db: Database = request.app.state.db
    settings: Settings = request.app.state.settings
    stale_after: float = settings.stalled_worker_seconds
    checks: dict[str, CheckStatus] = {"web": "ok"}
    backlog: Backlog | None = None

    try:
        async with db.sessions() as session:
            beating = await _worker_beating(session, stale_after)
            backlog = await _backlog(session)
    except Exception:
        # Both reads are the database check: they are all this endpoint asks of it, and a
        # database it cannot query is one the worker cannot be asked about either.
        log.exception("health: database check failed")
        checks["database"] = "error"
        checks["worker"] = "skipped"
    else:
        checks["database"] = "ok"
        checks["worker"] = "ok" if beating else "down"

    healthy = all(check == "ok" for check in checks.values())
    body: dict[str, object] = {"status": "ok" if healthy else "degraded", "checks": checks}
    # Siblings of ``checks`` rather than members, deliberately: every member of ``checks``
    # can turn the response 503, and neither a backlog nor a missing key must do that.
    if backlog is not None:
        body["backlog"] = backlog
    # Reported even when the database check failed: it is a settings read that cannot fail
    # with it, and a box degraded for two reasons should say both.
    body["llm_credential"] = _llm_credential(settings)
    return JSONResponse(body, status_code=200 if healthy else 503)


@router.get(
    "/api/debug/error",
    dependencies=[limited("debug", lambda settings: settings.debug_error_rate_limit)],
)
async def debug_error() -> None:
    """Fail on purpose: hitting this in production must produce a Sentry event."""
    raise RuntimeError("deliberate backend error to check Sentry")


def _llm_credential(settings: Settings) -> LlmCredential:
    """The provider question, asked where the answer is kept rather than restated here."""
    return "configured" if llm.credential_configured(settings) else "missing"


async def _worker_beating(session: AsyncSession, stale_after: float) -> bool:
    """Has any worker beaten recently enough to be counted alive?

    Any, not all: the stack needs a worker, not a particular one, and a box mid-restart
    briefly has two. Nothing here writes, so the check cannot itself queue or stall.
    """
    beating = await session.scalar(_BEATING_WORKERS, {"seconds": stale_after})
    return bool(beating)


async def _backlog(session: AsyncSession) -> Backlog:
    """How much work is queued and fetchable, and how long the oldest of it has waited.

    Only work that could run now counts. The nightly sweeps sit in ``todo`` from the
    moment they are deferred until their cron time comes round, and counting those would
    report a permanent backlog nobody is waiting on.
    """
    row = (await session.execute(_BACKLOG)).one()
    return Backlog(
        waiting=row.waiting,
        oldest_wait_seconds=round(float(row.oldest), 3) if row.oldest is not None else None,
    )
