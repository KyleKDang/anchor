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
from anchor.prose import Evidence
from export import Row
from faketmdb import FilmFixture
from library import film

IMPORTED = FilmFixture(8200, "The Film Being Imported", release_date="2011-05-01")

FORMING = pytest.mark.settings(readiness_forming_films=3, readiness_forming_bands=1)

EVIDENCE = Evidence(
    anchors=["4.0 stars: Film 01 (1981)"],
    loved=["Film 00 (1980)"],
    disliked=[],
    criteria=[],
    constraints=[],
    dismissed=[],
    rated_films=5,
    judgments=4,
)


async def _spend_on(owner, tmdb, seam, provider, *, output_tokens):
    """One prose regeneration for the signed-in owner, priced at the mid tier's $10/Mtok out.

    Through the seam rather than by inserting a row, so what the check reports is what
    the cap gate would have summed.
    """
    tmdb.with_films(*flows.LIBRARY)
    await flows.scale(owner, size=5)
    account = uuid.UUID(await flows.account_id(owner))
    provider.costs(input_tokens=0, output_tokens=output_tokens)
    await seam.regenerate_prose_profile(account, EVIDENCE)
    return account


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
    assert "llm_spend" not in body
    # The credential is a settings read, so it survives what the backlog does not: a box
    # degraded for two reasons should say both.
    assert body["llm_credential"] == "missing"


async def test_a_health_check_enqueues_nothing_and_writes_no_probe(client, db, jobs_app):
    """The check is a read, and the table it used to write to is gone.

    Nothing it does leaves a job row for the nightly sweep to clear, and the probe table
    itself is dropped by migration rather than left behind to fill.
    """
    await _register_worker(jobs_app)

    assert (await client.get("/api/health")).status_code == 200

    assert await jobs_app.job_manager.list_jobs_async() == []
    assert await _table_exists(db, "worker_probes") is False


async def test_a_box_with_no_llm_credential_says_so_without_failing_the_check(jobs_app, client):
    """#109: a keyless box and a box with nothing to suggest looked identical from outside.

    Discovery's empty state is honest for a shelf that has run out, and production wore it
    for a pipeline that had never run - the credential reached the container by no route at
    all. The signal belongs beside ``backlog`` rather than in ``checks``: dev and CI run
    keyless on purpose, and ``docker compose up --wait`` reads this endpoint.
    """
    await _register_worker(jobs_app)

    response = await client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "llm_credential" not in body["checks"]
    assert body["llm_credential"] == "missing"


@pytest.mark.settings(anthropic_api_key="sk-ant-not-a-real-key")
async def test_a_configured_box_says_so_and_never_says_the_key(jobs_app, client):
    await _register_worker(jobs_app)

    response = await client.get("/api/health")

    assert response.json()["llm_credential"] == "configured"
    assert "sk-ant-not-a-real-key" not in response.text


# --- What the month has cost, against both caps ---


async def test_an_unspent_month_reports_zero_against_both_caps(jobs_app, client):
    await _register_worker(jobs_app)

    body = (await client.get("/api/health")).json()

    assert body["llm_spend"] == {
        "platform": {"month_to_date_usd": 0.0, "cap_usd": 10.0},
        "accounts": {"highest_month_to_date_usd": 0.0, "cap_usd": 2.0, "at_cap": 0},
    }


@FORMING
async def test_health_reports_month_to_date_spend_against_both_caps(
    owner, tmdb, jobs_app, client, seam, provider
):
    """#123: a spent cap silences prose at INFO, and nothing outside the box said so.

    The credential and the backlog were already here, and between them they could not
    answer "why is prose empty": a cap behaving correctly leaves both looking fine. The
    numbers are the ones the cap gate reads, so a cap about to be hit is visible from
    outside before it is.
    """
    await _register_worker(jobs_app)
    # 1200 output tokens at the mid tier's $10 per million: what #123's one row cost.
    await _spend_on(owner, tmdb, seam, provider, output_tokens=1200)

    body = (await client.get("/api/health")).json()

    assert "llm_spend" not in body["checks"]
    assert body["llm_spend"] == {
        "platform": {"month_to_date_usd": 0.012, "cap_usd": 10.0},
        "accounts": {"highest_month_to_date_usd": 0.012, "cap_usd": 2.0, "at_cap": 0},
    }


@FORMING
async def test_an_account_at_its_cap_is_counted_without_being_named(
    owner, tmdb, jobs_app, client, seam, provider
):
    """The endpoint is unauthenticated, so it says how many accounts are capped, never which."""
    await _register_worker(jobs_app)
    account = await _spend_on(owner, tmdb, seam, provider, output_tokens=200_000)

    response = await client.get("/api/health")

    assert response.json()["llm_spend"]["accounts"] == {
        "highest_month_to_date_usd": 2.0,
        "cap_usd": 2.0,
        "at_cap": 1,
    }
    assert response.json()["llm_spend"]["platform"]["month_to_date_usd"] == 2.0
    assert str(account) not in response.text


async def test_shared_spend_counts_for_the_platform_and_no_account(
    jobs_app, client, seam, provider
):
    """A quality tag is nobody's: it moves the platform number and no account's."""
    await _register_worker(jobs_app)
    # 2000 output tokens at the cheap tier's $5 per million, batched at half price.
    provider.costs(input_tokens=0, output_tokens=2000).will_say(qualities=[])

    await seam.tag_film_qualities(film(9001), ("Acting",))

    body = (await client.get("/api/health")).json()
    assert body["llm_spend"]["platform"]["month_to_date_usd"] == 0.005
    assert body["llm_spend"]["accounts"]["highest_month_to_date_usd"] == 0.0
