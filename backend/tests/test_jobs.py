"""Background jobs ride the data change's transaction and run in the worker.

These sit one step below the API seam on purpose: a rolled-back enqueue has no
HTTP surface, so the harness itself (session, queue, inline worker) is under test.
"""

import logging
import re
import time
import uuid
from datetime import timedelta

import pytest
from procrastinate.jobs import Status
from procrastinate.manager import JobManager
from procrastinate.utils import utcnow
from sqlalchemy import func, select

import export
import flows
from anchor import jobs
from anchor.models import AuthSession
from export import Row
from faketmdb import FilmFixture

SWEPT = jobs.prune_expired_sessions
"""The pairing these transaction tests ride on: an expired login session, and the nightly
job that clears it. Small, real, and observable by the row's absence afterwards."""


def _lapsed_session(account_id):
    return AuthSession(
        token_hash=uuid.uuid4().hex,
        account_id=uuid.UUID(account_id),
        expires_at=utcnow() - timedelta(days=1),
    )


async def _lapsed_sessions(db):
    async with db.sessions() as session:
        return list(
            await session.scalars(select(AuthSession).where(AuthSession.expires_at <= func.now()))
        )


async def _todo_jobs(jobs_app):
    """Queued sweeps (the worker's own periodic jobs are not under test here)."""
    return [
        job
        for job in await jobs_app.job_manager.list_jobs_async()
        if job.status == "todo" and job.task_name == jobs.task_name(SWEPT)
    ]


async def test_rolled_back_data_change_takes_its_job_with_it(owner, db, jobs_app):
    account = await flows.account_id(owner)
    async with db.sessions() as session:
        session.add(_lapsed_session(account))
        await session.flush()
        await jobs.enqueue(session, jobs_app, SWEPT, timestamp=0)
        await session.rollback()

    assert await _lapsed_sessions(db) == []
    assert await _todo_jobs(jobs_app) == []


async def test_committed_data_change_and_its_job_run_in_the_worker(owner, db, jobs_app, run_jobs):
    account = await flows.account_id(owner)
    async with db.sessions() as session:
        session.add(_lapsed_session(account))
        await session.flush()
        await jobs.enqueue(session, jobs_app, SWEPT, timestamp=0)
        await session.commit()

    [queued] = await _todo_jobs(jobs_app)
    assert queued.task_kwargs == {"timestamp": 0}
    assert len(await _lapsed_sessions(db)) == 1

    await run_jobs()

    assert await _lapsed_sessions(db) == []
    assert await _todo_jobs(jobs_app) == []


# --- A worker that never came back ---

WEDGED = FilmFixture(7400, "The Job Whose Worker Died", release_date="2015-03-01", popularity=20.0)


@pytest.fixture
def stocked(tmdb):
    return tmdb.with_films(WEDGED)


async def _wedge(jobs_app):
    """Leave the queue exactly as a SIGKILLed worker leaves it, and return the job.

    A worker registers itself, fetches a job - which flips the row to ``doing`` and
    stamps the worker's id on it - and then the kernel takes the process. Nothing else
    happens: no heartbeat, no status, no unregister. These are the same two
    ``JobManager`` calls the worker makes before it runs anything, and stopping after
    them is the whole of the wedge.

    Cancelling an in-process worker would not reproduce it. That is a *graceful*
    shutdown: procrastinate aborts the running job and writes it a final status on the
    way out, which is precisely what the OOM killer denies it.
    """
    manager = jobs_app.job_manager
    worker_id = await manager.register_worker()
    job = await manager.fetch_job(queues=None, worker_id=worker_id)
    assert job is not None, "nothing was queued to wedge"
    assert await manager.get_job_status_async(job.id) is Status.DOING
    return job


async def _job(jobs_app, job_id):
    [job] = await jobs_app.job_manager.list_jobs_async(id=job_id)
    return job


def _reported(caplog):
    return [record.getMessage() for record in caplog.records if record.name == "anchor.jobs"]


@pytest.mark.settings(stalled_job_seconds=0)
async def test_a_job_left_running_by_a_dead_worker_is_requeued_and_re_runs(
    owner, stocked, jobs_app, defer, run_jobs, caplog
):
    """The wedge #61 was filed for: the import that never finished and never failed.

    A retry needs the task to *return* a failure, and SIGKILL gives the process no
    chance to mark anything - so ``retry=3`` protects against TMDB going down mid-row
    and against nothing else. The sweep is what delivers that same retry for the one
    failure mode the task could not report, and the requeued job runs to completion
    because every task reachable here re-reads its own state before doing work.
    """
    await flows.upload_export(
        owner, export.export(ratings=(Row(WEDGED.title, WEDGED.year, rating=4.0),))
    )
    wedged = await _wedge(jobs_app)
    assert wedged.task_name == jobs.task_name(jobs.match_import_rows)

    await defer(jobs.reclaim_stalled_jobs, timestamp=0)
    with caplog.at_level(logging.ERROR, logger="anchor.jobs"):
        await run_jobs()

    reclaimed = await _job(jobs_app, wedged.id)
    # Two attempts: the one the killed worker took to its grave, and the one that
    # finished the work. A job that ran once and succeeded would read one.
    assert (reclaimed.status, reclaimed.attempts) == ("succeeded", 2)
    assert (await flows.import_state(owner))["status"] == "complete"
    assert flows.bands_of(await flows.rated(owner)) == {WEDGED.tmdb_id: 4.0}

    # Not silent: an ERROR record is what Sentry's logging integration ships as an event.
    [reported] = _reported(caplog)
    assert "requeued" in reported


@pytest.mark.settings(stalled_job_seconds=0)
async def test_a_requeued_job_still_runs_when_its_retry_is_stamped_ahead_of_postgres(
    owner, stocked, jobs_app, defer, run_jobs, monkeypatch
):
    """#67: the sweep stamps the retry from the app's clock, the fetch reads Postgres's.

    Two machines: Postgres runs in Docker Desktop's VM, whose clock drifts from the Mac's.
    Let the app's clock lead the database's by more than the gap between the sweep's commit
    and the worker's next fetch - tens of milliseconds is enough - and the requeued job is
    not yet fetchable when the worker looks, which under ``wait=False`` is the last look it
    takes. A whole second of skew stands in for that drift, because the drift itself only
    shows on some days and this has to be red on every one of them.
    """
    await flows.upload_export(
        owner, export.export(ratings=(Row(WEDGED.title, WEDGED.year, rating=4.0),))
    )
    wedged = await _wedge(jobs_app)

    retry_job = JobManager.retry_job

    async def retry_a_second_late(self, job, retry_at=None, **kwargs):
        await retry_job(self, job, retry_at=utcnow() + timedelta(seconds=1), **kwargs)

    monkeypatch.setattr(JobManager, "retry_job", retry_a_second_late)

    await defer(jobs.reclaim_stalled_jobs, timestamp=0)
    await run_jobs()

    reclaimed = await _job(jobs_app, wedged.id)
    assert (reclaimed.status, reclaimed.attempts) == ("succeeded", 2)
    assert (await flows.import_state(owner))["status"] == "complete"


async def test_a_drain_that_cannot_finish_gives_up_and_names_what_is_left(jobs_app, run_jobs):
    """The fixture waits for jobs it is owed, and nothing in Anchor is owed an hour from now.

    Retry stamps say "now" and the periodic tasks are deferred at their cron time, so a job
    scheduled into the future is one no amount of waiting can satisfy. Only a test can put
    one there - and when one does, the fixture has to end the test rather than hold the
    suite open until CI's own timeout kills it with nothing to read.
    """
    await jobs_app.configure_task(
        name=jobs.task_name(jobs.retrain_taste_profile), schedule_in={"hours": 1}
    ).defer_async(account_id=str(uuid.uuid4()))

    started = time.monotonic()
    with pytest.raises(AssertionError, match=re.escape(jobs.task_name(jobs.retrain_taste_profile))):
        await run_jobs()
    assert time.monotonic() - started < 15


@pytest.mark.settings(stalled_job_seconds=0)
async def test_a_job_with_no_retry_left_is_failed_rather_than_left_running(
    owner, db, jobs_app, defer, run_jobs, caplog
):
    """Reclamation delivers the task's own retry policy; it does not invent a new one.

    The nightly sweep declares no retry, so a sweep whose worker died is failed rather
    than re-run - the same end the task would have reached had its process lived long
    enough to raise, and tomorrow night's run clears what this one did not. Either way
    the row leaves ``doing``, which is the ticket's bar.
    """
    account = await flows.account_id(owner)
    async with db.sessions() as session:
        session.add(_lapsed_session(account))
        await session.flush()
        await jobs.enqueue(session, jobs_app, SWEPT, timestamp=0)
        await session.commit()

    wedged = await _wedge(jobs_app)
    assert wedged.task_name == jobs.task_name(SWEPT)

    await defer(jobs.reclaim_stalled_jobs, timestamp=0)
    with caplog.at_level(logging.ERROR, logger="anchor.jobs"):
        await run_jobs()

    assert (await _job(jobs_app, wedged.id)).status == "failed"
    assert len(await _lapsed_sessions(db)) == 1
    [reported] = _reported(caplog)
    assert "failed" in reported


@pytest.mark.settings(stalled_job_seconds=0)
async def test_the_sweep_leaves_the_worker_running_it_alone(jobs_app, defer, run_jobs, caplog):
    """A threshold of zero makes every heartbeat look stale, this worker's own included.

    A sweep that took that at face value would requeue the job it is itself running, and
    beside it any long job on the same worker - and a CPU-bound retrain blocks the event
    loop past the heartbeat interval, so that is not a hypothetical. The worker running
    the sweep is alive by definition, whatever its last heartbeat says.
    """
    await defer(jobs.reclaim_stalled_jobs, timestamp=0)
    with caplog.at_level(logging.ERROR, logger="anchor.jobs"):
        await run_jobs()

    [sweep] = await jobs_app.job_manager.list_jobs_async(
        task=jobs.task_name(jobs.reclaim_stalled_jobs)
    )
    # One attempt, which is what a job that ran once reads. Two would mean the sweep
    # had requeued itself on the way past.
    assert (sweep.status, sweep.attempts) == ("succeeded", 1)
    assert _reported(caplog) == []
