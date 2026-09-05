"""The health check: web, database, and the worker proven by its own heartbeat.

The worker check is a read rather than a round trip (#82), so a worker is alive whatever
it happens to be doing. The tests stage the worker where the check reads it - registered
in the queue's own worker table, holding whatever job it holds - rather than racing a
real worker into a busy state that lasts only as long as its job does.
"""

import uuid

import pytest
from sqlalchemy import text

import export
import flows
from anchor import jobs
from export import Row
from faketmdb import FilmFixture

IMPORTED = FilmFixture(8200, "The Film Being Imported", release_date="2011-05-01")


async def _register_worker(jobs_app):
    """Register a worker and stamp its first beat, as a worker process does on the way up."""
    return await jobs_app.job_manager.register_worker()


async def _table_exists(db, name):
    async with db.sessions() as session:
        return bool(
            await session.scalar(text("SELECT to_regclass(:name) IS NOT NULL"), {"name": name})
        )


async def test_health_crosses_web_database_and_worker(client, worker):
    response = await client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"] == {"web": "ok", "database": "ok", "worker": "ok"}


async def test_a_worker_mid_import_is_healthy_rather_than_timed_out(owner, tmdb, jobs_app, client):
    """#82: the check must not call a busy worker a dead one.

    The import is real and the worker holding it is registered and beating, which is the
    state an owner importing their library leaves behind for as long as it runs. The old
    check proved the worker by enqueueing a probe and waiting for it to come back, so the
    probe sat behind this import and the check reported the worker ``timeout``, the stack
    ``degraded``, and 503 - which is enough to fail a deploy that lands at the same time.
    """
    tmdb.with_films(IMPORTED)
    await flows.upload_export(
        owner, export.export(ratings=(Row(IMPORTED.title, IMPORTED.year, rating=4.0),))
    )
    worker_id = await _register_worker(jobs_app)
    in_flight = await jobs_app.job_manager.fetch_job(queues=None, worker_id=worker_id)
    assert in_flight is not None
    assert in_flight.task_name == jobs.task_name(jobs.match_import_rows)

    response = await client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"]["worker"] == "ok"


async def test_health_degrades_when_no_worker_is_beating(client):
    response = await client.get("/api/health")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"] == {"web": "ok", "database": "ok", "worker": "down"}


@pytest.mark.settings(stalled_worker_seconds=0)
async def test_a_registered_worker_that_stopped_beating_is_down(client, jobs_app):
    """Registration is not liveness: a worker whose last beat is too old is a dead one."""
    await _register_worker(jobs_app)

    response = await client.get("/api/health")

    assert response.status_code == 503
    assert response.json()["checks"]["worker"] == "down"


async def test_a_deep_queue_is_reported_without_failing_the_check(jobs_app, defer, client):
    """Backlog is information, never a failure - a busy queue must not gate a deploy."""
    await _register_worker(jobs_app)
    for _ in range(5):
        await defer(jobs.retrain_taste_profile, account_id=str(uuid.uuid4()))

    response = await client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "backlog" not in body["checks"]
    assert body["backlog"]["waiting"] == 5
    assert body["backlog"]["oldest_wait_seconds"] >= 0


async def test_an_idle_queue_reports_an_empty_backlog(jobs_app, client):
    await _register_worker(jobs_app)

    body = (await client.get("/api/health")).json()

    assert body["backlog"] == {"waiting": 0, "oldest_wait_seconds": None}


async def test_a_job_scheduled_for_later_is_not_counted_as_waiting(jobs_app, client):
    """The nightly sweeps sit in ``todo`` until their cron time; nothing is waiting on them."""
    await _register_worker(jobs_app)
    await jobs_app.configure_task(
        name=jobs.task_name(jobs.prune_expired_sessions), schedule_in={"hours": 1}
    ).defer_async(timestamp=0)

    body = (await client.get("/api/health")).json()

    assert body["backlog"] == {"waiting": 0, "oldest_wait_seconds": None}


async def test_a_database_failure_skips_the_worker_check(client, app, monkeypatch):
    def refuse(*args, **kwargs):
        raise RuntimeError("postgres is gone")

    monkeypatch.setattr(app.state.db, "sessions", refuse)

    response = await client.get("/api/health")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"] == {"web": "ok", "database": "error", "worker": "skipped"}
    assert "backlog" not in body


async def test_a_health_check_enqueues_nothing_and_writes_no_probe(client, db, jobs_app):
    """The check is a read: it leaves no job row for the nightly sweep to clear, and the
    probe table it used to write to is gone by migration."""
    await _register_worker(jobs_app)

    assert (await client.get("/api/health")).status_code == 200

    assert await jobs_app.job_manager.list_jobs_async() == []
    assert await _table_exists(db, "worker_probes") is False
