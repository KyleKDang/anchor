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
from typing import TYPE_CHECKING, Any

import procrastinate
from procrastinate import JobContext, builtin_tasks
from procrastinate.jobs import Job, Status
from procrastinate.retry import RetryStrategy
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from anchor import catalog, matching, seeding
from anchor.db import Database
from anchor.errors import ApiError
from anchor.models import (
    BUILT_IN_QUALITIES,
    AuthSession,
    Import,
    ImportRow,
    ImportRowState,
    ImportStatus,
)
from anchor.settings import Settings
from anchor.tmdb import FilmNotInTmdb, Tmdb, TmdbUnavailable

if TYPE_CHECKING:
    # For the annotation only. The web process imports this module to *enqueue*, and
    # importing the LLM seam at runtime for a type would undo the structural rule that
    # only the worker loads it (architecture.md) - so the name is available to mypy and
    # to nobody else.
    from anchor.llm import Llm

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


def worker_context(db: Database, tmdb: Tmdb, llm: "Llm", settings: Settings) -> dict[str, Any]:
    """What every job receives as its ``additional_context``; read back with the getters below."""
    return {"db": db, "tmdb": tmdb, "llm": llm, "settings": settings}


def database_of(context: JobContext) -> Database:
    return context.additional_context["db"]  # type: ignore[no-any-return]


def tmdb_of(context: JobContext) -> Tmdb:
    return context.additional_context["tmdb"]  # type: ignore[no-any-return]


def llm_of(context: JobContext) -> "Llm":
    return context.additional_context["llm"]  # type: ignore[no-any-return]


def settings_of(context: JobContext) -> Settings:
    return context.additional_context["settings"]  # type: ignore[no-any-return]


async def enqueue(
    session: AsyncSession,
    jobs: procrastinate.App,
    task: TaskFunction,
    *,
    lock: str | None = None,
    queueing_lock: str | None = None,
    **kwargs: Any,
) -> int:
    """Enqueue ``task`` in the session's open transaction.

    The job row commits or rolls back together with the session's data changes,
    so a data change can never be persisted with its follow-up job lost.

    Jobs sharing a ``lock`` run one at a time, in the order they were enqueued. Only one
    job sharing a ``queueing_lock`` may be waiting at a time; a second raises
    :class:`procrastinate.exceptions.AlreadyEnqueued`, from a failed insert the caller
    has to have wrapped in a savepoint.
    """
    connection = await session.connection()
    raw = await connection.get_raw_connection()
    deferrer = jobs.configure_task(
        name=task_name(task),
        connection=raw.driver_connection,
        lock=lock,
        queueing_lock=queueing_lock,
    )
    return await deferrer.defer_async(**kwargs)


async def schedule_retrain(
    session: AsyncSession, jobs: procrastinate.App, account_id: uuid.UUID
) -> None:
    """Queue this account's retrain alongside the change that made it necessary.

    Called from every flow that writes a band, a rank, or an anchor mark, and inside that
    flow's own transaction: a rating that lands with its retrain lost would leave the
    taste profile quietly describing an ordering that no longer exists.
    The account lock keeps two retrains from regenerating the same artifacts at once.

    A burst of changes coalesces into one retrain. The job rebuilds the profile from
    scratch off the ordering as it stands, so while one is still *waiting* for this
    account a second would only repeat it - and a drag session on the wall is a dozen
    drops in a minute. The queueing lock is the queue's own "one waiting per key": a
    retrain already running does not count, since it may have read the ordering before
    this change, and the change owes a fresh one behind it.

    The refused insert has to fail inside a savepoint: Postgres aborts the whole
    transaction on a constraint violation otherwise, and the change this rides with
    would be lost along with the duplicate job.
    """
    try:
        async with session.begin_nested():
            await enqueue(
                session,
                jobs,
                retrain_taste_profile,
                lock=str(account_id),
                queueing_lock=f"retrain:{account_id}",
                account_id=str(account_id),
            )
    except procrastinate.exceptions.AlreadyEnqueued:
        return


async def schedule_prose_check(
    session: AsyncSession, jobs: procrastinate.App, account_id: uuid.UUID
) -> None:
    """Ask whether the prose profile is now due, alongside a change that is not an ordering one.

    Every change to the *ordering* already schedules a retrain, and the retrain asks this
    question on its way out - so nothing that moves a film needs to call this. What does
    is the other kind of trigger taste-profile.md names: a picker or constraint edit,
    which changes what a regeneration must respect without moving anything it describes.

    The job re-asks :func:`anchor.prose.due` itself, so calling this when nothing has
    accumulated costs a queue row and a query rather than a provider call.
    """
    await enqueue(session, jobs, regenerate_prose, lock=str(account_id), account_id=str(account_id))


async def schedule_restock(
    session: AsyncSession, jobs: procrastinate.App, account_id: uuid.UUID
) -> None:
    """Queue the discovery restock, from either of the two triggers it has.

    Both are engagement-gated, which is the whole of the feed's economy (discovery.md):
    the owner arriving at the feed, and a prose-profile bump - which is itself only ever
    reached by an account doing enough to earn a regeneration. An owner who ignores
    discovery causes neither, and costs nothing.

    The job re-asks :func:`anchor.feed.due` itself, so queueing one that has nothing to do
    costs a queue row and a query rather than a restock. The account lock keeps two of
    them from sourcing and reranking the same account at once.
    """
    await enqueue(
        session, jobs, restock_discovery, lock=str(account_id), account_id=str(account_id)
    )


async def retrain_taste_profile(context: JobContext, account_id: str) -> None:
    """Regenerate the account's weight vector and exemplar set, and record the retrain.

    Off the request path deliberately: the owner is answering the next comparison while
    this runs, and nothing they can see is waiting on it.

    The trainer is imported here rather than at the top of the module, so that only the
    worker ever loads it. The web process imports this module to *enqueue*, and pulling
    numpy and the whole feature pipeline into it for that would be a structural claim
    nobody meant to make - the same rule architecture.md puts on the LLM module.

    The prose profile is the third artifact and is deliberately not regenerated here.
    The other two cost milliseconds and are rebuilt every time; this one costs money, so
    all that happens here is the question of whether enough has accumulated to be worth
    asking - and a job of its own, under the same account lock, if it has. Keeping the
    call out of this job also keeps the retrain fast: nothing that follows a placement
    should be waiting on a provider.
    """
    from anchor import prose, taste

    async with database_of(context).sessions() as session:
        account = uuid.UUID(account_id)
        await taste.retrain(session, account)
        if await prose.due(session, account, settings_of(context)) is not None:
            await schedule_prose_check(session, context.app, account)
        await session.commit()


async def regenerate_prose(context: JobContext, account_id: str) -> None:
    """Rewrite the owner-readable prose profile, if it is still worth doing.

    The seam is imported inside the function for architecture.md's structural rule: the
    web process imports this module to enqueue, and must not load the LLM module by doing
    so. That rule is what makes "no interactive screen waits on an LLM call" a fact about
    the deployment rather than a promise about the code.

    Everything the provider might refuse - a spent cap, no credential, a provider that is
    down - arrives as ``Skipped``, and the answer to all of it is to leave the live
    version alone. That is the whole degradation story: the owner sees the prose they
    already had, with the last-updated line it already carried, and nothing tells them
    anything went wrong, because from their side nothing did.
    """
    from anchor import llm as llm_module
    from anchor import prose, qualities

    db, seam = database_of(context), llm_of(context)
    account = uuid.UUID(account_id)
    async with db.sessions() as session:
        # Re-asked rather than trusted: this job may have waited behind another
        # regeneration on the same account lock, and that one may have just answered it.
        trigger = await prose.due(session, account, settings_of(context))
        if trigger is None:
            return
        mark = await prose.watermark(session, account)
        evidence = await prose.evidence(session, account)

    try:
        text = await seam.regenerate_prose_profile(account, evidence)
    except llm_module.Skipped as skipped:
        log.info("prose profile for %s not regenerated: %s", account_id, skipped)
        return

    async with db.sessions() as session:
        await prose.record(session, account, text=text, trigger=trigger, mark=mark)
        # Queued rather than called, so the guess is a job of its own: it can fail and
        # retry without the prose being rewritten again, and the prose landing is what
        # decides there is something new to guess from. Same account lock, so it waits
        # for this job rather than racing it.
        if await qualities.picker_unanswered(session, account):
            await enqueue(
                session,
                context.app,
                refresh_quality_suggestions,
                lock=str(account),
                account_id=account_id,
            )
        # The bump is the discovery cache's invalidation: every verdict was keyed to the
        # version that just stopped being live, so the batch rerank is scheduled here, at
        # the one place a version is ever created (taste-profile.md).
        await schedule_restock(session, context.app, account)
        await session.commit()


async def refresh_quality_suggestions(context: JobContext, account_id: str) -> None:
    """Re-guess which of the owner's qualities to pre-tick, while the picker is unanswered.

    Bought on the prose profile's trigger rather than one of its own, because it is the
    same question a regeneration just asked - what does the evidence say this owner
    likes - and the moment that is worth paying to answer is the moment this is too.

    Once the owner has answered the picker there is nothing left to guess: their answer
    is the answer, so the guessing stops for good rather than being made and ignored.
    That is also why the check is re-asked after the call - they may have answered while
    the provider was thinking, and a guess landing on top of their answer would tick
    boxes they deliberately left empty.

    Skipped exactly as the prose is. A guess is the most optional thing Anchor buys, so a
    spent cap or a provider that is down leaves the last one standing and says nothing.
    """
    from anchor import llm as llm_module
    from anchor import prose, qualities

    db, seam = database_of(context), llm_of(context)
    account = uuid.UUID(account_id)
    async with db.sessions() as session:
        if not await qualities.picker_unanswered(session, account):
            return
        evidence = await prose.evidence(session, account)
        listed = [entry.name for entry in await qualities.listing(session, account)]

    try:
        suggested = await seam.suggest_qualities(account, evidence, listed)
    except llm_module.Skipped as skipped:
        log.info("quality suggestions for %s not refreshed: %s", account_id, skipped)
        return

    async with db.sessions() as session:
        if await qualities.picker_unanswered(session, account):
            await qualities.record_suggestions(session, account, suggested)
            await session.commit()


async def tag_film(context: JobContext, tmdb_id: int) -> None:
    """Buy one film's quality tags, unless somebody already has.

    The seam is imported inside the function for architecture.md's structural rule, the
    same as the regeneration above: the web process imports this module to enqueue, and
    must not load the LLM module by doing so.

    Idempotent, because every task on this queue can be re-run from the top by the
    stalled-job sweep and because two accounts can place the same film at once. The stamp
    is checked before the call and again with the write, so a film is bought once even
    when two jobs get past the first check together - and the write is one transaction,
    so a film is never stamped tagged with its tags missing.

    A cap, a missing credential or a provider that is down all arrive as ``Skipped``, and
    the answer to all of them is to leave the film untagged and try again another day:
    none of them cost anything, and nothing degrades visibly, because criteria selection
    falls back to the quality rotation - which is what it did before any film had tags.

    An answer that does not parse is the opposite case and is treated as the opposite
    way round. The ledger row for it is already written, so leaving the film untagged
    would put it back in front of the next placement that touches it, and the next, each
    one paying again for the same unusable answer - the one shape this feature could
    quietly run up a bill in. So the film is stamped as tagged with nothing, which is the
    answer the fallback already copes with, and the bug is reported rather than retried:
    a schema failure is a bug in a prompt (llm.py), and ERROR is what reaches Sentry.
    """
    from anchor import llm as llm_module
    from anchor import tags

    db, seam = database_of(context), llm_of(context)
    async with db.sessions() as session:
        film = await tags.pending(session, tmdb_id)
    if film is None:
        return

    try:
        named = await seam.tag_film_qualities(film, BUILT_IN_QUALITIES)
    except llm_module.Skipped as skipped:
        log.info("film %s not tagged: %s", tmdb_id, skipped)
        return
    except llm_module.BadAnswer:
        log.exception("the tagging prompt got an answer it cannot read; film %s", tmdb_id)
        named = []

    async with db.sessions() as session:
        await tags.record(session, tmdb_id, named)
        await session.commit()


async def restock_discovery(context: JobContext, account_id: str) -> None:
    """Rebuild the discovery shelf: source, prefilter, rerank what is unjudged, refill.

    The pipeline is imported here rather than at the top of the module for the rule the
    trainer and the seam are imported under: the web process imports this module to
    *enqueue*, and must not load the code that can spend money by doing so.

    Everything a provider or TMDB might refuse is handled inside the pipeline, and the
    answer to all of it is the same - build the shelf out of the verdicts that do exist.
    That is the degraded state discovery.md describes, and from the owner's side it looks
    like a slightly shorter shelf and nothing else.
    """
    from anchor import feed

    await feed.restock(
        database_of(context),
        tmdb_of(context),
        llm_of(context),
        uuid.UUID(account_id),
        settings_of(context),
    )


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
        # A seed import of any real size crosses both readiness bars at once
        # (onboarding-and-import.md), and this is the moment it does: arming here is what
        # earns the import both dots and lets its completion screen name what just
        # unlocked. Idempotent, so a retried job arms nothing twice.
        #
        # Imported here rather than at the top of the module: the tier pulls in the
        # feature pipeline, and the web process imports this module only to enqueue.
        from anchor import tier

        await tier.note_unlock(session, record.account_id, settings)
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
    """Nightly hygiene: drop the rows of jobs that have finished, succeeded or failed."""
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


async def reclaim_stalled_jobs(context: JobContext, timestamp: int) -> None:
    """Deal with the jobs of workers that died holding them.

    A worker the OS kills - the kernel's OOM killer, a `docker kill`, a box that reboots
    - never gets to mark the job it was running. The row stays ``doing`` forever: no
    retry, because a retry needs the task to *return* a failure; no failure; and no
    trace anywhere but a direct query on the queue table. Every long job inherits this,
    the import being merely the first one heavy enough to get its worker killed.

    Procrastinate already makes the death visible: each worker registers itself and
    beats a heartbeat, and ``get_stalled_jobs`` reports the jobs of workers that have
    stopped beating. Nothing acts on that list, which is what this is.

    What it does is deliver the retry policy the task already declared, for the one
    failure mode the task could not report itself: a job with attempts left goes back to
    ``todo``, one without is marked ``failed``. It invents no policy of its own, and it
    ends every wedge either way. The price is that a reclaimed job re-runs from the top,
    so every task on this queue has to be idempotent - the import re-reads its pending
    rows and skips what it already applied, and the nightly jobs re-derive from scratch.
    """
    manager = context.app.job_manager
    stalled = await manager.get_stalled_jobs(
        seconds_since_heartbeat=settings_of(context).stalled_job_seconds
    )
    for job in stalled:
        if _is_this_worker(context, job):
            continue
        if _has_attempts_left(context.app, job):
            await manager.retry_job(job)
            outcome = "requeued"
        else:
            await manager.finish_job(job, status=Status.FAILED, delete_job=False)
            outcome = "failed"
        # ERROR rather than WARNING deliberately: sentry_sdk's logging integration turns
        # an ERROR record into an event, and being told is half of what #61 asked for.
        # A wedged job that only a query on procrastinate_jobs would find is the bug.
        log.error(
            "job %s (%s) was left running by a worker that stopped answering;"
            " %s after %s attempt(s)",
            job.id,
            job.task_name,
            outcome,
            job.attempts,
        )


def _is_this_worker(context: JobContext, job: Job) -> bool:
    """Is this job held by the worker running the sweep, which is alive by definition?

    Its heartbeat can still look stale: the beat shares the worker's event loop, and a
    CPU-bound retrain blocks that loop for as long as it runs. Reclaiming on the strength
    of a heartbeat this worker was too busy to send would requeue jobs that are running
    perfectly well - the sweep's own included.
    """
    held_by = context.job.worker_id
    return held_by is not None and job.worker_id == held_by


def _has_attempts_left(app: procrastinate.App, job: Job) -> bool:
    """The task's own ``retry=`` budget, read the way procrastinate reads it on a failure.

    A task that declares no retry is not given one here: its author said a failure is
    final, and a worker dying is a failure. Bounding it also stops the one loop that
    would matter - a job heavy enough to kill every worker that picks it up, requeued
    forever by the sweep that keeps finding it.
    """
    task = app.tasks.get(job.task_name)
    strategy = task.retry_strategy if task else None
    if not isinstance(strategy, RetryStrategy) or strategy.max_attempts is None:
        return False
    return job.attempts < strategy.max_attempts


def _declare_tasks() -> procrastinate.Blueprint:
    tasks = procrastinate.Blueprint()
    tasks.task(name=retrain_taste_profile.__name__, pass_context=True)(retrain_taste_profile)
    # Retried, because the whole job is one long conversation with a provider and the far
    # end goes down. Re-running is safe: the first thing it does is re-ask whether the
    # regeneration is still due, and a version that landed answers that with no.
    tasks.task(name=regenerate_prose.__name__, retry=2, pass_context=True)(regenerate_prose)
    # Retried for the same reason and safe for the same one: it re-asks whether the picker
    # is still unanswered before it spends, and a second guess simply replaces the first.
    tasks.task(name=refresh_quality_suggestions.__name__, retry=2, pass_context=True)(
        refresh_quality_suggestions
    )
    # Retried for the same reason, and safe for the same reason: a re-run re-reads the
    # stamp, and a film that got tagged in between answers with nothing left to do.
    tasks.task(name=tag_film.__name__, retry=2, pass_context=True)(tag_film)
    # Retried, because the whole job is one long conversation with TMDB and the far end
    # goes down. Every row commits on its own, so a retry resumes rather than repeats.
    tasks.task(name=match_import_rows.__name__, retry=3, pass_context=True)(match_import_rows)
    # Retried, because it is a long conversation with both outside services at once. Safe
    # to repeat: every window's verdicts commit as they land and a re-run skips whatever
    # is already judged, so the second attempt buys only what the first one missed.
    tasks.task(name=restock_discovery.__name__, retry=2, pass_context=True)(restock_discovery)
    scheduled_tasks = [
        # Every minute, because the window between a worker dying and the next sweep is
        # time an owner spends looking at an import that says it is still running.
        (reclaim_stalled_jobs, "* * * * *"),
        (remove_old_jobs, "0 4 * * *"),
        (prune_expired_sessions, "10 4 * * *"),
        (resync_stale_films, "30 4 * * *"),
    ]
    for scheduled, cron in scheduled_tasks:
        task = tasks.task(
            name=scheduled.__name__, queueing_lock=scheduled.__name__, pass_context=True
        )(scheduled)
        tasks.periodic(cron=cron)(task)
    return tasks
