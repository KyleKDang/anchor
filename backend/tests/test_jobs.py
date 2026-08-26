"""Background jobs ride the data change's transaction and run in the worker.

These sit one step below the API seam on purpose: a rolled-back enqueue has no
HTTP surface, so the harness itself (session, queue, inline worker) is under test.
"""

from sqlalchemy import select

from anchor import jobs
from anchor.models import WorkerProbe


async def _probes(db):
    async with db.sessions() as session:
        return list(await session.scalars(select(WorkerProbe)))


async def _todo_jobs(jobs_app):
    """Queued probe answers (the worker's own periodic jobs are not under test here)."""
    return [
        job
        for job in await jobs_app.job_manager.list_jobs_async()
        if job.status == "todo" and job.task_name == jobs.task_name(jobs.answer_probe)
    ]


async def test_rolled_back_data_change_takes_its_job_with_it(db, jobs_app):
    async with db.sessions() as session:
        probe = WorkerProbe()
        session.add(probe)
        await session.flush()
        await jobs.enqueue(session, jobs_app, jobs.answer_probe, probe_id=str(probe.id))
        await session.rollback()

    assert await _probes(db) == []
    assert await _todo_jobs(jobs_app) == []


async def test_committed_data_change_and_its_job_run_in_the_worker(db, jobs_app, run_jobs):
    async with db.sessions() as session:
        probe = WorkerProbe()
        session.add(probe)
        await session.flush()
        await jobs.enqueue(session, jobs_app, jobs.answer_probe, probe_id=str(probe.id))
        await session.commit()

    [queued] = await _todo_jobs(jobs_app)
    assert queued.task_kwargs == {"probe_id": str(probe.id)}
    [unanswered] = await _probes(db)
    assert unanswered.answered_at is None

    await run_jobs()

    [answered] = await _probes(db)
    assert answered.answered_at is not None
    assert await _todo_jobs(jobs_app) == []
