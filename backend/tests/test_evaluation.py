"""The operator's evaluation queries, run against a scripted stretch of owner activity.

Evaluation reads every recorded event and feeds nothing (ADR 0012), so this suite has two
halves that look nothing alike.

The first is one long replay: an owner rates a library, fills a backlog, watches films the
tier put in front of them and films it did not, answers the discovery shelf three ways,
lets a seat go stale, and moves a film on the wall after it landed. Then every documented
query in ``backend/sql/evaluation`` is run against the database that stretch produced and
its numbers are asserted. The queries are read off disk rather than restated here, so the
text under test is the text the operator runs - a query that drifts from its file is not
tested by a copy of what it used to say.

The second half is structural, and it is the half ADR 0012 actually turns on: nothing in
``src/`` may read the metrics table or the queries, and nothing ships a surface for them.
An indicator that fed the engine back would be a different product, and the only way to
keep saying it does not is to assert that no code path exists.
"""

import re
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text, update

import export
import flows
from anchor.models import Account
from export import Row
from faketmdb import FilmFixture
from flows import (
    accept,
    account_id,
    add_to_backlog,
    build_ordering,
    discovery,
    dismiss_suggestion,
    log_watches,
    mark_anchor,
    mark_watched,
    move,
    rate,
    seen_it,
    shelf,
    tier_ids,
    upload_export,
)

QUERIES = Path(__file__).resolve().parents[1] / "sql" / "evaluation"
"""The documented queries, versioned in the repo and run from disk by these tests."""

BARS = {
    "readiness_forming_films": 3,
    "readiness_forming_bands": 2,
    "readiness_ready_films": 6,
    "prose_placements_trigger": 2,
}
"""Bars a six-film library clears, so the replay reaches *ready* without rating fifty."""

PIPELINE = {
    "discovery_shelf": 3,
    "discovery_shortlist": 8,
    "discovery_min_votes": 0,
    "discovery_rerank_window": 10,
}
"""A small feed: one rerank window, so a scripted ranking is the whole ranking."""

DAMPING = {
    "tier_staleness_watches": 2,
    "tier_enter_cooldown": 0,
    "tier_reentry_cooldown": 99,
    "tier_hysteresis": 0.0,
}
"""Staleness at two watches, so the replay can afford a rotation; no re-entry after it,
so the film that rotated stays out and the count is the one the test made."""

pytestmark = pytest.mark.settings(**BARS, **PIPELINE, **DAMPING)


def films(start, count, genre, title):
    return tuple(
        FilmFixture(start + n, f"{title} {n:02d}", genres=(genre,), directors=(genre,))
        for n in range(count)
    )


WESTERNS = films(9000, 3, "Western", "Western")
HORRORS = films(9100, 3, "Horror", "Horror")
RATED = WESTERNS + HORRORS
"""The library the replay starts from: every Western above every Horror."""

BACKLOG = films(9200, 4, "Western", "Backlog")
"""Films the owner queued by hand, which the tier is then free to seat."""

CANDIDATES = films(9300, 6, "Western", "Candidate")
"""Untracked films TMDB offers back, so the discovery shelf has something to hold."""

SPARE = films(9400, 6, "Comedy", "Spare")
"""Films that exist only to be watched: the only way a test moves the watch clock."""

CATALOG = RATED + BACKLOG + CANDIDATES + SPARE


@pytest.fixture(autouse=True)
def stocked(tmdb):
    return tmdb.with_films(*CATALOG).with_neighbours(WESTERNS[0].tmdb_id, *CANDIDATES)


def ranked(*fixtures, fit="strong_fit"):
    """One scripted rerank answer covering every candidate, at one bucket."""
    return {
        "ranked": [
            {"tmdb_id": film.tmdb_id, "fit": fit, "explanation": f"Because of {film.title}."}
            for film in fixtures
        ]
    }


# --- Running the documented SQL ---


async def run(db, name, **params):
    """Execute one documented query verbatim and hand back its rows as dicts.

    Shares and rates come back as ``Decimal``, because the queries divide in ``numeric``
    rather than in floating point; they are floats here so the assertions read as maths.
    """
    sql = (QUERIES / f"{name}.sql").read_text()
    async with db.sessions() as session:
        result = await session.execute(text(sql), params)
        return [
            {
                key: float(value) if isinstance(value, Decimal) else value
                for key, value in row.items()
            }
            for row in result.mappings()
        ]


async def only(db, name, account, **params):
    """The one row of ``name`` belonging to ``account``, which must be there."""
    rows = [row for row in await run(db, name, **params) if row["account_id"] == account]
    assert len(rows) == 1, f"{name} returned {len(rows)} rows for the account"
    return rows[0]


def body(path):
    """A query with its commentary stripped: the SQL that actually runs.

    The prose above a query is allowed to name what the query must not do - saying "no
    targets here" is the documentation working - so the scans below read the statement.
    """
    lines = path.read_text().lower().splitlines()
    return "\n".join(line for line in lines if not line.strip().startswith("--"))


# --- The replay ---


async def a_stretch_of_activity(owner, run_jobs, provider):
    """Everything the indicators are meant to see, in the order an owner would do it.

    Returned as the handful of facts the assertions need: the account, and which films
    were watched from a tier seat, watched off the owner's own bat, and answered on the
    shelf. The rest of the state is left where it belongs, in the database.
    """
    provider.will_say(**ranked(*CANDIDATES))

    # A library across two bands, then the fit and the first prose written from it.
    await build_ordering(owner, WESTERNS, band=4.0)
    await build_ordering(owner, HORRORS, band=2.0)
    await mark_anchor(owner, WESTERNS[0])
    await mark_anchor(owner, HORRORS[-1])
    await run_jobs()
    account = uuid.UUID(await account_id(owner))

    # A backlog the tier can seat, and a look at the Watchlist to seat it.
    for film in BACKLOG:
        await add_to_backlog(owner, film)
    seated = tier_ids(await flows.tier(owner))
    assert BACKLOG[0].tmdb_id in seated, "the tier seated the backlog it was given"

    # A watch off a tier seat, rated: the engine's pick, and its landing.
    from_the_tier = BACKLOG[0]
    await rate(owner, from_the_tier, 4.5)

    # A watch the tier had nothing to do with, rated: the owner's own pick, same window.
    hand_picked = SPARE[0]
    await rate(owner, hand_picked, 3.0)

    # The discovery shelf: two arrivals, because nothing on the request path waits on a
    # provider - the first queues the restock, the second is the boundary it lands at.
    await discovery(owner)
    await run_jobs()
    standing = await shelf(owner)
    assert len(standing) == 3, "a full shelf to answer three different ways"
    by_id = {film.tmdb_id: film for film in CATALOG}
    accepted, dismissed, already_seen = (by_id[card["tmdb_id"]] for card in standing)
    await accept(owner, accepted)
    await dismiss_suggestion(owner, dismissed)
    await seen_it(owner, already_seen)

    # The accepted film, watched and rated: the discovery funnel end to end.
    await rate(owner, accepted, 5.0)

    # Watches nobody picked off a seat, until what was passed over goes stale. The first
    # read is a reload rather than an arrival, so it shows the seats without rotating them.
    assert tier_ids(await flows.tier(owner, boundary=False)), "seats still held, and passed over"
    await log_watches(owner, SPARE[1:4])
    assert not tier_ids(await flows.tier(owner)), "every seat went stale and rotated out"

    return account, from_the_tier, hand_picked, accepted


# --- The named indicator set ---


async def test_tier_adoption_is_the_tier_sourced_share_of_logged_watches(
    owner, db, run_jobs, provider
):
    """Denominated in watches, which is the only opportunity the tier ever gets."""
    account, *_ = await a_stretch_of_activity(owner, run_jobs, provider)

    row = await only(db, "tier_adoption", account)
    assert row["tier_sourced_watches"] == 2, "the seated backlog film and the accepted one"
    assert row["logged_watches"] == row["tier_sourced_watches"] + row["other_watches"]
    assert row["tier_adoption"] == pytest.approx(
        row["tier_sourced_watches"] / row["logged_watches"]
    )
    assert 0 < row["tier_adoption"] < 1, "some of the watches came off a seat, and some did not"


async def test_rotation_rate_is_staleness_demotions_per_watch(owner, db, run_jobs, provider):
    """The tier's own clock: rotations against the watches they were measured in."""
    account, *_ = await a_stretch_of_activity(owner, run_jobs, provider)

    row = await only(db, "rotation_rate", account)
    assert row["staleness_rotations"] >= 1, "the replay let at least one seat go stale"
    assert row["logged_watches"] > 0
    assert row["rotations_per_watch"] == pytest.approx(
        row["staleness_rotations"] / row["logged_watches"]
    )


async def test_accept_and_dismissal_are_rates_per_restock(owner, db, run_jobs, provider):
    """Per restock, never per week: a feed nobody opens buys nothing and rates nothing."""
    account, *_ = await a_stretch_of_activity(owner, run_jobs, provider)

    row = await only(db, "discovery_rates", account)
    assert row["restocks"] == 1, "one restock, which is what the two arrivals bought"
    assert row["accepts"] == 1
    assert row["dismissals"] == 1
    assert row["accepts_per_restock"] == pytest.approx(1.0)
    assert row["dismissals_per_restock"] == pytest.approx(1.0)


async def test_the_discovery_funnel_counts_accepted_then_watched_then_rated(
    owner, db, run_jobs, provider
):
    """The seen-it is deliberately not in it: it was never a watch discovery caused."""
    account, *_ = await a_stretch_of_activity(owner, run_jobs, provider)

    row = await only(db, "discovery_funnel", account)
    assert (row["accepted"], row["watched"], row["rated"]) == (1, 1, 1)
    assert row["watched_share"] == pytest.approx(1.0)
    assert row["rated_share"] == pytest.approx(1.0)


async def test_landings_compare_engine_picks_with_same_window_hand_picked_watches(
    owner, db, run_jobs, provider
):
    """The ground truth: where a watched pick sits in the ordering, against the owner's own."""
    account, *_ = await a_stretch_of_activity(owner, run_jobs, provider)

    row = await only(db, "landings", account, watches_per_window=100)
    assert row["engine_placed"] == 2, "both engine-sourced watches were rated"
    assert row["owner_placed"] >= 1, "and the hand-picked watch landed too"
    assert row["engine_landing"] > row["owner_landing"], (
        "the engine's picks landed higher in the ordering than the owner's own"
    )
    # The watches waiting in the rate-later queue: a fact that is open rather than absent,
    # and the context the means above have to be read against.
    assert row["owner_unplaced"] >= 4, "the watches nobody has placed yet are counted apart"
    assert row["engine_unplaced"] == 0


async def test_landings_make_the_same_comparison_for_the_discovery_feed(
    owner, db, run_jobs, provider
):
    """The second half of the ground truth: an accepted film against a hand-added one.

    Read off the origin stamp rather than the standing, so it is a different question from
    the tier's - and one watch can honestly answer both, which is what an accepted film
    that took a seat before it was watched does.
    """
    account, *_ = await a_stretch_of_activity(owner, run_jobs, provider)

    row = await only(db, "landings", account, watches_per_window=100)
    assert row["discovery_placed"] == 1, "the accepted film was watched and placed"
    assert row["hand_added_placed"] >= 1, "against the films the owner brought themselves"
    assert row["discovery_landing"] > row["hand_added_landing"], (
        "the feed's pick landed higher than the owner's own hand-added watches"
    )


async def test_a_landing_is_read_at_computation_time_so_a_later_move_counts(
    owner, db, run_jobs, provider
):
    """A rate-later placement, or a move months later, completes the fact when it happens."""
    account, from_the_tier, _, _ = await a_stretch_of_activity(owner, run_jobs, provider)

    before = (await only(db, "landings", account, watches_per_window=100))["engine_landing"]
    assert from_the_tier.tmdb_id in flows.ordering_of(await flows.rated(owner))[4.5], (
        "the film the tier picked landed where the owner put it"
    )

    # Months later, the owner changes their mind and drags it down the wall.
    await move(owner, from_the_tier, 2.5, 1)
    after = (await only(db, "landings", account, watches_per_window=100))["engine_landing"]

    assert after < before, "the move dropped it, and the indicator read the ordering as it now is"


async def test_an_imported_back_catalogue_is_not_an_opportunity_anybody_had(
    owner, db, run_jobs, tmdb
):
    """A diary row is history, not a watch the tier or the feed was ever in the room for.

    It is a real watch event and it moves the watch clock, exactly as seeding.py intends.
    What it is not is an opportunity: nothing could have put those films in front of the
    owner, so counting them in a denominator would read years of Letterboxd as a tier that
    was passed over the whole time.
    """
    tmdb.with_films(*RATED, *SPARE)
    await upload_export(
        owner,
        export.export(
            ratings=tuple(Row(film.title, 1999, rating=4.0) for film in RATED),
            diary=tuple(
                Row(film.title, 1999, watched_date=f"2024-04-{n + 1:02d}")
                for n, film in enumerate(RATED)
            ),
        ),
    )
    await run_jobs()
    account = uuid.UUID(await account_id(owner))

    # One watch the owner logs here and now, on top of the imported history.
    await rate(owner, SPARE[0], 3.0)

    row = await only(db, "tier_adoption", account)
    assert row["watches_before_the_account"] == len(RATED), "the diary rows are set aside"
    assert row["logged_watches"] == 1, "and the denominator is the watch Anchor was there for"

    rotation = await only(db, "rotation_rate", account)
    assert rotation["watches_before_the_account"] == len(RATED), "the same history, set aside"
    assert rotation["logged_watches"] == 1, "and the same denominator: the one watch Anchor saw"

    landings = await only(db, "landings", account, watches_per_window=100)
    assert landings["owner_placed"] == 1, "only the watch the owner logged here can land"
    assert landings["hand_added_placed"] == 1, "and it was hand-added, not import-seeded"


async def test_a_window_is_a_stretch_of_the_watch_clock(owner, db, run_jobs, provider):
    """ "Same window" is the whole of the comparison, so the partition has to be real.

    Numbered off every watch event the account has, imported history included, because
    that is what the watch clock is - and it is what keeps a back catalogue out of the
    windows the engine's picks land in.
    """
    account, *_ = await a_stretch_of_activity(owner, run_jobs, provider)

    whole = await run(db, "landings", watches_per_window=100)
    split = [
        row
        for row in await run(db, "landings", watches_per_window=3)
        if row["account_id"] == account
    ]
    assert len(split) > 1, "a smaller window cuts the same watches into more of them"
    assert [row["window_index"] for row in split] == sorted({row["window_index"] for row in split})

    one = next(row for row in whole if row["account_id"] == account)
    counted = ("engine_placed", "engine_unplaced", "owner_placed", "owner_unplaced")
    for column in counted:
        assert sum(row[column] for row in split) == one[column], (
            f"{column} is the same watches however they are windowed"
        )


async def test_every_indicator_is_denominated_in_opportunities(owner, db, run_jobs, provider):
    """No calendar time anywhere: every denominator is a count of things that happened."""
    account, *_ = await a_stretch_of_activity(owner, run_jobs, provider)

    denominators = {
        "tier_adoption": ("logged_watches",),
        "rotation_rate": ("logged_watches",),
        "discovery_rates": ("restocks",),
        "discovery_funnel": ("accepted",),
    }
    for name, columns in denominators.items():
        row = await only(db, name, account)
        for column in columns:
            assert isinstance(row[column], int), f"{name}.{column} counts events, not days"

    for path in sorted(QUERIES.glob("*.sql")):
        statement = body(path)
        for banned in ("interval", "now()", "current_date", "current_timestamp", "date_trunc"):
            assert banned not in statement, f"{path.name} reaches for calendar time"


async def test_the_demo_account_is_excluded_from_every_aggregate(
    owner, other_owner, db, run_jobs, provider
):
    """The demo account's activity is a showroom, and no indicator may read it."""
    account, *_ = await a_stretch_of_activity(owner, run_jobs, provider)

    demo = uuid.UUID(await account_id(other_owner))
    await build_ordering(other_owner, WESTERNS, band=4.0)
    await mark_watched(other_owner, SPARE[0])
    async with db.sessions() as session:
        await session.execute(update(Account).where(Account.id == demo).values(is_demo=True))
        await session.commit()

    for path in sorted(QUERIES.glob("*.sql")):
        name = path.stem
        params = {"watches_per_window": 100} if name == "landings" else {}
        accounts = {row["account_id"] for row in await run(db, name, **params)}
        assert demo not in accounts, f"{name} counted the demo account"
        assert account in accounts, f"{name} lost the real account"


# --- Reads, but never feeds ---


SOURCE = Path(__file__).resolve().parents[1] / "src" / "anchor"


def test_no_engine_code_path_reads_the_metrics_table():
    """ADR 0012's whole point: measurement and learning are separate consumers.

    The metrics row is written at each retrain and read by nobody. A scorer that read its
    own accuracy back, or a tier that read an adoption rate, would be the engine learning
    from its own report card - which is the one thing the design forbids outright.
    """
    writers = set()
    readers = set()
    for path in SOURCE.glob("*.py"):
        source = path.read_text()
        if "TasteMetrics" not in source and "taste_metrics" not in source:
            continue
        if path.name == "models.py":
            continue
        # A construction is a write; anything else that names the table is a read.
        if re.search(r"\bTasteMetrics\(", source):
            writers.add(path.name)
        if re.search(r"select\([^)]*TasteMetrics", source) or re.search(
            r"from\s+taste_metrics", source
        ):
            readers.add(path.name)

    assert writers == {"taste.py"}, f"the metrics row has one writer, not {sorted(writers)}"
    assert not readers, f"an engine module reads the metrics table: {sorted(readers)}"


def test_no_application_code_reads_the_evaluation_queries():
    """The queries are operator artifacts. Nothing imports them, so nothing can act on them.

    This is what makes "evaluation reads but never feeds" structural rather than a promise:
    the results have no path into the process at all, because the process cannot load them.
    """
    scanned = 0
    for path in SOURCE.rglob("*.py"):
        source = path.read_text()
        assert "sql/evaluation" not in source, f"{path.name} reaches for the evaluation queries"
        # Citing evaluation.md in a docstring is the design being followed; naming a
        # query file is the firewall being crossed. Only the second is checked for.
        assert not re.search(r"""['"][^'"]*\.sql['"]""", source), f"{path.name} names a query file"
        scanned += 1
    assert scanned > 20, "the scan found the application's modules"


def test_nothing_ships_an_admin_surface_for_the_indicators(app):
    """No admin UI, no targets, no alerting (evaluation.md). Directional reading only."""
    paths = list(_paths(app.routes))
    assert "/api/discovery" in paths, "the scan is reading the app's real routes"
    for path in paths:
        assert "metric" not in path, f"an evaluation surface shipped at {path}"
        assert "evaluation" not in path, f"an evaluation surface shipped at {path}"

    for path in sorted(QUERIES.glob("*.sql")):
        statement = body(path)
        for banned in ("target", "threshold", "alert"):
            assert banned not in statement, f"{path.name} states a {banned}, which is forbidden"


def _paths(routes):
    """Every path the app answers on, including the ones behind an included router.

    FastAPI wraps ``include_router`` in a router object rather than flattening its routes
    into the app, so a scan that only reads ``app.routes`` sees the docs endpoints and
    nothing else - which would pass this test by seeing nothing at all.
    """
    for route in routes:
        if hasattr(route, "path"):
            yield route.path
        original = getattr(route, "original_router", None)
        if original is not None:
            yield from _paths(original.routes)
