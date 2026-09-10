"""The demo account's fixture build: the authored outcome, replayed through the product.

The fixture states the target; the build imports the ratings and applies every move, mark
and action through the real API and the real jobs (demo-account.md, "Build and refresh").
So the assertion is the one a visitor could make: read every surface back through the API
and find exactly what the fixture authored, band by band and rank by rank - plus the three
things only the build can be blamed for. The flag goes on last, so the replay itself is the
proof that no bypass exists: a flagged account refuses every one of these writes (#42). A
rebuild replaces the previous account rather than accumulating beside it. And the build's
spend is re-scoped to the shared ledger, so a rebuild neither hides what it cost nor lets
the demo's monthly cap reset with every deploy.

The taste itself is asserted only where a rule can be stated: sixty to eighty films (ADR
0015), every band anchored, more than one band hand-ordered. Whether it has edges is the
owner's reading, and the ticket's look gate.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

import flows
from anchor import demobuild, llm
from anchor.models import BANDS, Account, SpendLedgerEntry
from faketmdb import FilmFixture
from invariants import assert_ordering_well_formed, placement_clocks, prose_versions

FIXTURE = demobuild.Fixture.load()

PIPELINE = {
    "discovery_shortlist": 12,
    "discovery_rerank_window": 12,
    "discovery_min_votes": 0,
    # The client's self-throttle is for TMDB's benefit, and a hundred films through a fake
    # would otherwise spend a minute per build waiting on it.
    "tmdb_requests_per_second": 10_000,
}
"""One rerank window, so the shelf is scripted with one answer rather than three."""

pytestmark = pytest.mark.settings(**PIPELINE)

PROSE = "You want a film to hold the shot and say nothing."


def _fixture_film(film: demobuild.FixtureFilm) -> FilmFixture:
    return FilmFixture(
        film.tmdb_id,
        film.title,
        release_date=f"{film.year}-06-01",
        genres=tuple(film.genres),
        directors=(film.director,),
        original_language=film.language,
        # Spread the default order out, so the authored order is not the default by luck.
        vote_average=5.0 + (film.tmdb_id % 40) / 10,
        vote_count=1000 + film.tmdb_id % 5000,
    )


CANDIDATES = tuple(
    FilmFixture(
        9_900_000 + n,
        f"Candidate {n:02d}",
        release_date="2016-06-01",
        genres=("Drama", "Thriller"),
        directors=("Somebody Quiet",),
        vote_count=300,
    )
    for n in range(12)
)
"""What the feed can suggest: near every wall film, and never in the fixture."""


@pytest.fixture(autouse=True)
def stocked(tmdb):
    tmdb.with_films(*(_fixture_film(film) for film in FIXTURE.films))
    for band in FIXTURE.wall:
        for film in band.films:
            tmdb.with_neighbours(film.tmdb_id, *CANDIDATES)
    return tmdb


@pytest.fixture(autouse=True)
def scripted(provider):
    provider.will_say(paragraphs=[PROSE])
    for _ in range(3):
        provider.will_say(
            ranked=[
                {
                    "tmdb_id": film.tmdb_id,
                    "fit": "strong_fit",
                    "explanation": f"Because {film.title}.",
                }
                for film in CANDIDATES
            ]
        )
    return provider


async def a_built_demo(client, db, run_jobs):
    async def drain(_account):
        await run_jobs()

    return await demobuild.build(client, db, FIXTURE, drain=drain)


async def _visitor(client_from):
    """A fresh session opened through the one door the landing page offers."""
    visitor = client_from("10.0.0.7")
    entered = await visitor.post("/api/auth/demo")
    assert entered.status_code == 200, entered.text
    assert entered.json()["demo"] is True
    return visitor


# --- The fixture itself ---


def test_the_fixture_is_the_size_the_adr_fixed_and_names_its_sensibility():
    rated = [film for band in FIXTURE.wall for film in band.films]
    assert 60 <= len(rated) <= 80
    assert FIXTURE.sensibility.strip().endswith(".")
    assert FIXTURE.sensibility.count(". ") == 0, "one sentence, nameable in a breath"
    assert len(FIXTURE.edges) >= 5, "the arguable judgments are written down beside the films"


def test_every_band_carries_a_pool_of_anchors():
    assert sorted(band.band for band in FIXTURE.wall) == sorted(BANDS)
    for band in FIXTURE.wall:
        assert sum(film.anchor for film in band.films) >= 2, f"band {band.band} is under-anchored"


def test_every_staged_state_is_authored_once():
    assert any(film.imported_as is not None for band in FIXTURE.wall for film in band.films)
    assert sum(film.pinned for film in FIXTURE.backlog) >= 1
    assert sum(film.vetoed for film in FIXTURE.backlog) >= 1
    assert len(FIXTURE.rate_later) >= 1
    assert {rewatch.answer for rewatch in FIXTURE.rewatches} >= {None, "confirmed"}
    assert FIXTURE.criteria_session.answers >= 5
    assert 1 <= len(FIXTURE.qualities) <= 2
    assert FIXTURE.dismiss_suggestions >= 1


# --- The replay ---


async def test_the_replayed_wall_is_the_authored_wall_band_by_band_and_rank_by_rank(
    client, client_from, db, run_jobs
):
    account = await a_built_demo(client, db, run_jobs)
    visitor = await _visitor(client_from)

    wall = await flows.rated(visitor)
    assert [row["band"] for row in wall["rows"]] == [band.band for band in FIXTURE.wall]
    for authored, built in zip(FIXTURE.wall, wall["rows"], strict=True):
        assert [film["tmdb_id"] for film in built["films"]] == [
            film.tmdb_id for film in authored.films
        ], f"band {authored.band} is not in its authored order"
        assert [film["rank"] for film in built["films"]] == list(range(1, len(authored.films) + 1))
        assert [film["anchor"] for film in built["films"]] == [
            film.anchor for film in authored.films
        ]
        assert built["anchors"] == sum(film.anchor for film in authored.films)
    await assert_ordering_well_formed(db, account)

    # Hand-ordered, in the engine's own terms: a move landed in more than one band.
    clocks = await placement_clocks(db, account)
    moved_in = {
        band.band
        for band in FIXTURE.wall
        for film in band.films
        if clocks[film.tmdb_id][1] is not None
    }
    assert len(moved_in) >= 2

    # Every rating came in through the seed import and was then edited, never written.
    changed = (await flows.sync_list(visitor))["changed"]
    assert {(row["tmdb_id"], row["synced"], row["band"]) for row in changed} == {
        (film.tmdb_id, film.imported_as, band.band)
        for band in FIXTURE.wall
        for film in band.films
        if film.imported_as is not None
    }


async def test_every_living_state_is_staged(client, client_from, db, run_jobs):
    await a_built_demo(client, db, run_jobs)
    visitor = await _visitor(client_from)

    tier = await flows.tier(visitor, boundary=False)
    assert tier["unlocked"] is True
    pinned = {film["tmdb_id"] for film in tier["up_next"] if film["pinned"]}
    assert pinned == {film.tmdb_id for film in FIXTURE.backlog if film.pinned}
    assert {film["tmdb_id"] for film in tier["vetoed"]} == {
        film.tmdb_id for film in FIXTURE.backlog if film.vetoed
    }
    assert len(tier["up_next"]) + len(tier["pool"]) >= 10, "the tier is seated"

    # The backlog screen lists what the tier did not seat; together they are the fixture's.
    backlog = await flows.backlog(visitor)
    queued = {film["tmdb_id"] for film in backlog["films"]} | set(flows.tier_ids(tier))
    assert queued | {film["tmdb_id"] for film in tier["vetoed"]} == {
        film.tmdb_id for film in FIXTURE.backlog
    }

    rated = await flows.rated(visitor)
    assert {film["tmdb_id"] for film in rated["rate_later"]} == {
        film.tmdb_id for film in FIXTURE.rate_later
    }

    for rewatch in FIXTURE.rewatches:
        page = await flows.film_page(visitor, rewatch)
        if rewatch.answer is None:
            assert page["rewatch"] is not None, "the still-feel-the-same question is open"
            asked_at = datetime.fromisoformat(page["rewatch"]["watched_at"])
            expected = datetime.now(UTC) - timedelta(days=rewatch.days_ago)
            # An export carries dates, not times: the same day is the most it can say.
            assert abs(asked_at - expected) < timedelta(days=1), "dated relative to the build"
        else:
            assert page["rewatch"] is None, "the answered question is not asked again"

    session = await flows.film_page(visitor, FIXTURE.criteria_session)
    criteria = [judgment for judgment in session["judgments"] if judgment["kind"] == "criteria"]
    assert len(criteria) == FIXTURE.criteria_session.answers
    assert all(judgment["verdict"] != "skip" for judgment in criteria)

    picker = await flows.qualities(visitor)
    assert picker["answered"] is True
    assert {quality["name"] for quality in picker["qualities"] if quality["checked"]} == set(
        FIXTURE.qualities
    )

    dismissed = (await visitor.get("/api/discovery/dismissals")).json()["films"]
    assert len(dismissed) == FIXTURE.dismiss_suggestions

    warmup = (await visitor.get("/api/warmup")).json()
    assert warmup["fork"] is False, "the demo never lands on the entry fork"


async def test_the_prose_and_the_shelf_are_pipeline_output_bought_once_under_the_shared_scope(
    client, client_from, db, run_jobs, provider
):
    account = await a_built_demo(client, db, run_jobs)
    visitor = await _visitor(client_from)

    profile = await flows.profile(visitor)
    assert profile["prose"] is not None
    assert PROSE in profile["prose"]["text"], "the prose is what the provider wrote, verbatim"
    assert provider.asked_of(llm.PROSE_SYSTEM), "and it was bought through the seam"

    shelf = await flows.shelf(visitor, boundary=False)
    assert shelf, "the feed is stocked before anybody visits"
    assert {film["tmdb_id"] for film in shelf} <= {film.tmdb_id for film in CANDIDATES}

    async with db.sessions() as session:
        rows = (await session.execute(select(SpendLedgerEntry.account_id))).scalars().all()
    assert rows, "the build's spend is on the ledger"
    assert all(row is None for row in rows), "under the shared scope, not the demo's own"

    # Runtime cost is zero: a visitor walking the feed with the queue drained buys nothing.
    bought = provider.dispatched
    versions = await prose_versions(db, account)
    await flows.discovery(visitor)
    await flows.tier(visitor)
    await run_jobs()
    assert provider.dispatched == bought
    assert await prose_versions(db, account) == versions


async def test_a_rebuild_replaces_the_previous_demo_rather_than_joining_it(
    client, client_from, db, run_jobs
):
    first = await a_built_demo(client, db, run_jobs)
    second = await a_built_demo(client_from("10.0.0.8"), db, run_jobs)
    assert first != second

    async with db.sessions() as session:
        demos = (await session.scalars(select(Account).where(Account.is_demo))).all()
        assert [demo.id for demo in demos] == [second]
        assert demos[0].email == demobuild.DEMO_EMAIL
        assert demos[0].password_hash is None, "no credentials, so no login form reaches it"
        assert await session.get(Account, first) is None
        # The first build's spend outlived the account it was bought for.
        spent = await session.scalar(select(SpendLedgerEntry.id).limit(1))
        assert spent is not None

    visitor = await _visitor(client_from)
    assert (await visitor.get("/api/auth/me")).json()["id"] == str(second)


# --- The door ---


async def test_the_door_is_closed_until_a_demo_has_been_built(client):
    refused = await client.post("/api/auth/demo")
    assert refused.status_code == 404, refused.text
    assert refused.json()["error"]["code"] == "demo_unavailable"


async def test_entering_the_demo_needs_no_credentials_and_lands_a_read_only_session(
    client, client_from, db, run_jobs
):
    account = await a_built_demo(client, db, run_jobs)
    visitor = client_from("10.0.0.9")

    entered = await visitor.post("/api/auth/demo")
    assert entered.status_code == 200, entered.text
    assert entered.json() == {
        "id": str(account),
        "email": demobuild.DEMO_EMAIL,
        "verified": True,
        "demo": True,
    }
    assert "anchor_session" in entered.cookies

    me = await visitor.get("/api/auth/me")
    assert me.status_code == 200 and me.json()["demo"] is True
    refused = await visitor.post(f"/api/anchors/{FIXTURE.wall[0].films[0].tmdb_id}")
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "demo_read_only"

    # Unreachable through the login form, and its address cannot be signed up over.
    login = await visitor.post(
        "/api/auth/login", json={"email": demobuild.DEMO_EMAIL, "password": "anything at all"}
    )
    assert login.status_code == 401
    signup = await visitor.post(
        "/api/auth/signup", json={"email": demobuild.DEMO_EMAIL, "password": "anything at all"}
    )
    assert signup.status_code == 409


async def test_two_visitors_get_sessions_of_their_own(client, client_from, db, run_jobs):
    await a_built_demo(client, db, run_jobs)
    one = await _visitor(client_from)
    two = await _visitor(client_from)
    assert one.cookies["anchor_session"] != two.cookies["anchor_session"]
    logged_out = await one.post("/api/auth/logout")
    assert logged_out.status_code == 204
    assert (await two.get("/api/auth/me")).status_code == 200, "leaving is per visitor"


# --- Nothing reaches the realm but the API ---


def test_the_build_writes_no_content_row_directly():
    """The privileged writes are the account and its swap; the realm is the API's alone.

    Read off the source rather than the outcome, because an outcome can look right for a
    build that wrote it by hand - which is the drift demo-account.md rules out. The
    account-realm tables are never named in the module, so nothing in it can insert one.
    """
    source = demobuild.__file__
    with open(source) as handle:
        text = handle.read()
    for table in ("AccountFilm", "Placement", "ComparisonLogEntry", "WatchEvent", "Dismissal"):
        assert table not in text, f"the build reaches {table} directly"
