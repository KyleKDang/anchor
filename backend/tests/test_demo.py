"""The demo account's read-only enforcement: nothing writes to a flagged account.

Two halves, matching the two halves of the rule.

The first is the request guard, and it is asserted by enumeration rather than by example.
The suite asks the app itself which of its routes mutate and drives every one of them on a
flagged session, so a write path added later without the guard fails here instead of
shipping a hole - which is the whole reason the guard lives in the one auth door rather
than in each route.

The second is the engine's own maintenance, which is the part no route can be blamed for:
restocks, shelf rotation, retrains and tier maintenance all run off a visit, and a demo
account is nothing but visits. So the assertion is not that a particular job declined but
that a visitor walking the whole product, twice, with the queue drained behind them, leaves
the account's realm byte-for-byte as it was (demo-account.md).

Nothing here creates a demo account the way production will: the flag is set directly on an
ordinary lived-in account, because the fixture build and the door that opens onto it land
separately, and this slice must hold before either exists.

The exclusion from the evaluation aggregates is the third thing the flag does, and it is
asserted where the queries are, in ``test_evaluation.py``.
"""

import inspect
import re
import uuid
from pathlib import Path

import pytest
from sqlalchemy import update

import flows
from anchor import demo, jobs
from anchor.models import Account
from faketmdb import FilmFixture
from flows import (
    account_id,
    add_to_backlog,
    build_ordering,
    discovery,
    mark_anchor,
)
from invariants import assert_realm_unchanged, realm_snapshot

SOURCE = Path(__file__).resolve().parents[1] / "src" / "anchor"

BARS = {
    "readiness_forming_films": 3,
    "readiness_forming_bands": 2,
    "readiness_ready_films": 6,
    "prose_placements_trigger": 2,
}
"""Bars a six-film library clears, so the account reaches *ready* without rating fifty."""

PIPELINE = {
    "discovery_shelf": 3,
    "discovery_shortlist": 8,
    "discovery_min_votes": 0,
    "discovery_rerank_window": 10,
}

pytestmark = pytest.mark.settings(**BARS, **PIPELINE)


def films(start, count, genre, title):
    return tuple(
        FilmFixture(start + n, f"{title} {n:02d}", genres=(genre,), directors=(genre,))
        for n in range(count)
    )


WESTERNS = films(9000, 3, "Western", "Western")
HORRORS = films(9100, 3, "Horror", "Horror")
BACKLOG = films(9200, 2, "Western", "Backlog")
CANDIDATES = films(9300, 3, "Western", "Candidate")
CATALOG = WESTERNS + HORRORS + BACKLOG + CANDIDATES


@pytest.fixture(autouse=True)
def stocked(tmdb):
    return tmdb.with_films(*CATALOG).with_neighbours(WESTERNS[0].tmdb_id, *CANDIDATES)


def ranked(*fixtures):
    return {
        "ranked": [
            {"tmdb_id": film.tmdb_id, "fit": "strong_fit", "explanation": f"About {film.title}."}
            for film in fixtures
        ]
    }


# --- The account under the flag ---


async def a_lived_in_account(owner, run_jobs, provider):
    """An ordinary account with something on every surface, built before it is flagged.

    This is the shape the fixture build will hand over: an ordering across two bands, an
    anchor, a backlog the tier can seat, a prose profile, and a discovery shelf. Built
    through the real flows, because a demo assembled out of raw rows would be asserting
    something no visitor will ever see.
    """
    provider.will_say(**ranked(*CANDIDATES))
    await build_ordering(owner, WESTERNS, band=4.0)
    await build_ordering(owner, HORRORS, band=2.0)
    await mark_anchor(owner, WESTERNS[0])
    for film in BACKLOG:
        await add_to_backlog(owner, film)
    await run_jobs()

    # Two arrivals at the feed: the first queues the restock, the second is the boundary
    # it lands at. After this the account has a shelf, which is what visitors come for.
    await discovery(owner)
    await run_jobs()
    assert await flows.shelf(owner), "the account has a shelf before it becomes the demo"
    assert flows.tier_ids(await flows.tier(owner)), "and seats in the tier"
    return uuid.UUID(await account_id(owner))


async def flag_as_demo(db, account):
    async with db.sessions() as session:
        await session.execute(update(Account).where(Account.id == account).values(is_demo=True))
        await session.commit()


# --- Every write refuses ---


UNGUARDED = {
    ("POST", "/api/auth/signup"),
    ("POST", "/api/auth/verify"),
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/logout"),
}
"""The four that do not hang off the session door, and must not.

Signup, verification and login are how an account that is not logged in becomes one, so
they cannot depend on being logged in; each already refuses the demo by its own means, and
``test_auth.py`` is where that is asserted. Logout reads the cookie directly and always
succeeds, which is what lets a visitor put the demo down.
"""


def mutating_operations(app):
    """Ask the app which of its own routes mutate, so the table cannot go stale.

    Read off the OpenAPI schema rather than off a list in this file, because a list here
    would be a second thing to remember and the failure mode of forgetting it is a route
    silently going untested. The count is asserted against the source below, so a route
    hidden from the schema cannot hide from this either.
    """
    return sorted(
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        for method in operations
        if method.upper() in demo.WRITE_METHODS
    )


def a_path_like(path):
    """The route template with something plausible in each placeholder.

    The values do not have to resolve to anything. The guard runs as a dependency of the
    session door, which FastAPI solves before it validates the request's own parameters or
    body, so a demo session is refused on the way in and never reaches the handler that
    would have looked the id up. That ordering is exactly what the assertion below pins:
    if the refusal ever moved into the handlers, these calls would start coming back 404
    and 422 instead.
    """
    return (
        path.replace("{tmdb_id}", str(WESTERNS[0].tmdb_id))
        .replace("{offer_id}", str(uuid.uuid4()))
        .replace("{constraint_id}", str(uuid.uuid4()))
        .replace("{row_id}", str(uuid.uuid4()))
    )


def test_the_enumeration_sees_every_mutating_route_in_the_source(app):
    """The table's own guard: the schema and the routers agree on how many writes exist.

    Without this the enumeration could quietly shrink - a route excluded from the schema,
    a router left off the app - and the suite below would go on passing while covering
    less. Counting decorators is crude on purpose; it is checking that nothing vanished,
    not what anything does.
    """
    # Any router name, not just ``router``: one module carries a second one, and pinning
    # the name would have made this a check that nobody had added a third.
    decorator = re.compile(r"@[a-z_]+\.(?:post|put|patch|delete)\(")
    decorated = sum(len(decorator.findall(path.read_text())) for path in SOURCE.glob("*.py"))
    assert len(mutating_operations(app)) == decorated


async def test_every_mutating_route_refuses_a_demo_account(owner, app, db, run_jobs, provider):
    """One documented refusal, from every write path the app has, without exception.

    The point of enumerating rather than listing: a write path added later is covered the
    moment it is written, and one added without the guard fails here rather than shipping
    a hole for a visitor to find.
    """
    account = await a_lived_in_account(owner, run_jobs, provider)
    await flag_as_demo(db, account)

    refused = 0
    for method, path in mutating_operations(app):
        if (method, path) in UNGUARDED:
            continue
        response = await owner.request(method, a_path_like(path), json={})
        assert response.status_code == 403, f"{method} {path} answered {response.status_code}"
        assert response.json()["error"] == {"code": demo.CODE, "message": demo.MESSAGE}, (
            f"{method} {path} invented its own refusal"
        )
        refused += 1
    assert refused >= 30, "the enumeration found the app's write surface, not a corner of it"


async def test_the_demo_can_still_read_every_screen(owner, db, run_jobs, provider):
    """The other half of the same claim, and the one a blanket refusal would fail.

    A demo account that answered 403 to everything would satisfy the test above and be
    worth nothing: what is on offer is a lived-in account to look around.
    """
    account = await a_lived_in_account(owner, run_jobs, provider)
    await flag_as_demo(db, account)

    assert (await owner.get("/api/auth/me")).json()["demo"] is True
    assert flows.ordering_of(await flows.rated(owner)), "the wall is still there"
    assert await flows.anchors(owner), "and the anchors"
    assert (await owner.get("/api/watchlist/tier")).status_code == 200
    assert (await owner.get("/api/discovery")).status_code == 200
    assert (await owner.get("/api/profile")).status_code == 200
    assert (await owner.get(f"/api/films/{WESTERNS[0].tmdb_id}")).status_code == 200


async def test_logging_out_of_the_demo_still_works(owner, db, run_jobs, provider):
    """The one write a visitor must always be able to perform: putting the demo down."""
    account = await a_lived_in_account(owner, run_jobs, provider)
    await flag_as_demo(db, account)

    assert (await owner.post("/api/auth/logout")).status_code == 204
    assert (await owner.get("/api/auth/me")).status_code == 401


# --- The engine's own maintenance skips it ---


async def test_a_visitor_walking_the_whole_product_changes_nothing(owner, db, run_jobs, provider):
    """The account stays exactly as it was built, however many visitors walk through it.

    Every read here is one the engine treats as a session boundary: arriving at the feed
    rotates cards off the shelf and buys a restock, arriving at the Watchlist maintains the
    tier, and either can light a dot. The walk is done twice with the queue drained after
    it, because once through would not tell a skip apart from a first visit that had
    nothing to do yet.
    """
    account = await a_lived_in_account(owner, run_jobs, provider)
    await flag_as_demo(db, account)
    before = await realm_snapshot(db, account)

    for _ in range(2):
        await flows.tier(owner)
        await discovery(owner)
        await flows.unlocks(owner)
        await flows.rated(owner)
        await flows.profile(owner)
        await flows.backlog(owner)
        await run_jobs()

    assert_realm_unchanged(before, await realm_snapshot(db, account), "a walk through the demo")


ACCOUNT_SCOPED = (
    jobs.retrain_taste_profile,
    jobs.regenerate_prose,
    jobs.refresh_quality_suggestions,
    jobs.restock_discovery,
)
"""The engine's per-account jobs: the retrain, the two LLM refreshes, and the restock."""

SHOWS_ITS_WORK = (jobs.retrain_taste_profile, jobs.refresh_quality_suggestions)
"""The two whose skip can be watched happening on a settled account.

The prose regeneration and the restock are the other two, and neither has anything to do
on an account that was just built: each re-asks its own gate on the way in, and the gates
are satisfied by the build that produced the account. Deferring one of them here would
pass whether the skip existed or not, so it is not asserted here dishonestly. What holds
for all four instead is that none of them can be registered on the queue without the skip
- asserted structurally below - and that the gate the restock is actually held by is
guarded too, which the visitor's walk above proves by leaving the shelf where it was.
"""


@pytest.mark.parametrize("task", SHOWS_ITS_WORK, ids=lambda task: task.__name__)
async def test_the_engine_jobs_decline_a_demo_account(task, owner, db, defer, run_jobs, provider):
    """Asserted at the queue, which is the layer that holds whatever enqueued the job.

    The enqueue side is already closed - every flow that schedules one of these is behind
    the request guard or behind a visit gate that asks the same question - so this is
    about the job that is already on the queue when the flag arrives, and about the next
    caller nobody has thought of yet.

    Run once before the flag and once after, because these jobs each re-ask their own gate
    on the way in and a job that had nothing to do would assert nothing either way. The
    first run is what proves the second one means something: it has to move the realm, or
    the test says so rather than passing quietly.
    """
    account = await a_lived_in_account(owner, run_jobs, provider)

    idle = await realm_snapshot(db, account)
    await defer(task, account_id=str(account))
    await run_jobs()
    working = await realm_snapshot(db, account)
    assert working != idle, f"{task.__name__} had nothing to do, so the skip below proves nothing"

    await flag_as_demo(db, account)
    before = await realm_snapshot(db, account)
    await defer(task, account_id=str(account))
    await run_jobs()

    assert_realm_unchanged(before, await realm_snapshot(db, account), task.__name__)


def test_every_account_scoped_task_is_registered_with_the_skip(app):
    """The structural half: a per-account job cannot be registered without the guard.

    The wrap is applied where the tasks are declared, so this reads the registry the queue
    actually dispatches from rather than the definitions. A task that grows an
    ``account_id`` and is registered bare fails here, which is the only moment anybody
    would notice before a visitor did.
    """
    scoped = {
        name: task
        for name, task in app.state.jobs.tasks.items()
        if "account_id" in inspect.signature(task.func).parameters
    }
    bare = [name for name, task in scoped.items() if not getattr(task.func, "skips_demo", False)]
    assert not bare, f"account-scoped tasks registered without the demo skip: {sorted(bare)}"
    assert len(scoped) == len(ACCOUNT_SCOPED), (
        f"the queue has {len(scoped)} account-scoped tasks and this file names "
        f"{len(ACCOUNT_SCOPED)}; the list above has fallen behind"
    )
