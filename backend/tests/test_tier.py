"""The ranked tier as the owner meets it: the gate, the shape, the damping, the overrides.

Every test here drives the JSON API and asserts what the owner would see on the Watchlist
screen. Nothing asserts a score - there is no surface that carries one and no test may
invent one (ADR 0005) - so the engine's opinion is only ever read as an order.

Two levers make that order deterministic without naming a number. Films built alike score
alike, and the tier breaks a tie on the film's id, so a backlog of identical films seats
in id order. Where a test needs one film to genuinely outrank another it earns that the
way the product does: the rated ordering puts every Western above every Horror, so the
fit learns to prefer Westerns and the backlog's Westerns outrank its Horrors.

Time passes here only as the owner's own activity. Every cooldown and staleness measure
is denominated in the watch clock, so a test that needs the clock to move logs watches;
there is no calendar clock anywhere to freeze (testing.md).
"""

import uuid

import pytest
from sqlalchemy import text

import flows
from anchor import unlocks
from anchor.models import Unlock
from faketmdb import FilmFixture
from flows import (
    add_to_backlog,
    build_ordering,
    log_watches,
    rate,
    tier_ids,
)
from invariants import (
    assert_nothing_rating_shaped,
    comparison_log,
    exemplars,
    taste_metrics,
    watch_clock,
    watch_standings,
    weight_vector,
)

# --- The catalog ---


def films(start, count, genre, title):
    """A run of films alike in everything but their id, so only ``genre`` can separate them.

    Alike matters twice over: identical vote statistics leave the two prior columns with
    no spread and so no say, and identical credits and keywords contribute the same
    constant to every score. What is left is the genre, which is the one fact these tests
    ever ask the fit to have an opinion about.
    """
    return tuple(FilmFixture(start + n, f"{title} {n:02d}", genres=(genre,)) for n in range(count))


WESTERNS = films(7000, 3, "Western", "Western")
HORRORS = films(7100, 3, "Horror", "Horror")
RATED = WESTERNS + HORRORS
"""The owner's library, best first: every Western above every Horror."""

BACKLOG = films(7200, 40, "Horror", "Backlog")
"""The plain backlog. All alike, so the tier seats them in id order."""

RIVALS = films(7300, 10, "Western", "Rival")
"""Backlog films the fit prefers to :data:`BACKLOG` once Westerns are on top."""

SPARE = films(7400, 20, "Comedy", "Spare")
"""Films that exist only to be watched, which is how a test moves the watch clock."""

CATALOG = RATED + BACKLOG + RIVALS + SPARE

READY_BARS = {
    "readiness_forming_films": 3,
    "readiness_forming_bands": 2,
    "readiness_ready_films": 6,
}
"""Bars a six-film library clears, so a test can reach *ready* without rating fifty films."""


def tuned(**tier):
    """The ready bars plus this test's damping numbers, as one settings mark.

    One mark rather than two stacked: only the closest one is read, so the bars have to
    travel with whatever the test is actually tuning. Merged rather than double-splatted,
    so a test that is about the gate itself can move a bar as well as the damping.
    """
    return pytest.mark.settings(**{**READY_BARS, **tier})


PATIENT = {
    "tier_staleness_watches": 100,
    "tier_enter_cooldown": 0,
    "tier_reentry_cooldown": 0,
    "tier_hysteresis": 0.0,
}
"""Every damping mechanism switched off, for the tests that are about something else."""


@pytest.fixture(autouse=True)
def stocked(tmdb):
    return tmdb.with_films(*CATALOG)


async def make_ready(owner, run_jobs=None):
    """Rate enough, across enough bands, that the tier's gate opens.

    The Westerns land above the Horrors, which is the whole of the taste these tests use.
    Running the jobs trains the fit; a test that only cares about the tier's shape can
    skip it and let every film score alike.
    """
    await build_ordering(owner, WESTERNS, band=4.0)
    await build_ordering(owner, HORRORS, band=2.0)
    if run_jobs is not None:
        await run_jobs()


async def fill(owner, backlog):
    for film in backlog:
        await add_to_backlog(owner, film)


async def with_challengers_waiting(owner, run_jobs):
    """A full tier of Horror, with five Westerns the fit prefers sitting just outside it.

    Staged with vetoes rather than by turning the owner's taste over, because a veto is
    the one thing that keeps a film the engine wants out of a seat it would otherwise
    take. Lifting all five is then a shift the tier has to absorb: five better films
    arrive at once, and every damping mechanism gets its say over what happens next.
    """
    await fill(owner, BACKLOG[:30] + RIVALS[:5])
    await make_ready(owner, run_jobs)
    for film in RIVALS[:5]:
        await flows.veto(owner, film)
    assert tier_ids(await flows.tier(owner)) == [film.tmdb_id for film in BACKLOG[:30]]
    for film in RIVALS[:5]:
        await flows.lift_veto(owner, film)


def rivals_in(seated):
    return [tmdb_id for tmdb_id in seated if tmdb_id in {film.tmdb_id for film in RIVALS}]


# --- The gate ---


async def test_before_ready_the_screen_is_the_honestly_unranked_backlog(owner):
    """A fake ranking on day one would teach the owner the tier's opinion is worthless."""
    await fill(owner, BACKLOG[:3])

    payload = await flows.tier(owner)

    assert payload["readiness"] == "cold"
    assert payload["unlocked"] is False
    assert payload["up_next"] == []
    assert payload["pool"] == []
    assert [film["tmdb_id"] for film in (await flows.backlog(owner))["films"]] == [
        film.tmdb_id for film in reversed(BACKLOG[:3])
    ]
    assert_nothing_rating_shaped(payload, "the pre-gate watchlist")


@tuned()
async def test_the_pre_gate_screen_says_how_far_off_the_unlock_is(owner):
    """Ambient only: a line and a subtle bar, drawn from the engine's own thresholds."""
    empty = await flows.tier(owner)
    assert empty["progress"]["share"] == 0.0

    await make_ready(owner)

    payload = await flows.tier(owner)
    assert payload["unlocked"] is True
    assert payload["progress"] is None, "the bar is gone once there is a tier above it"
    assert {bar["dimension"] for bar in empty["progress"]["thresholds"]} == {
        "rated_films",
        "bands_spanned",
    }


async def test_progress_climbs_as_the_evidence_does(owner):
    await build_ordering(owner, WESTERNS, band=4.0)

    part_way = (await flows.tier(owner))["progress"]["share"]

    assert 0.0 < part_way < 1.0


@tuned()
async def test_an_override_is_refused_while_the_tier_is_locked(owner):
    """There is no queue to manage yet, and saying so beats pretending there is."""
    await fill(owner, BACKLOG[:3])

    await flows.pin(owner, BACKLOG[0], expect=409)
    await flows.veto(owner, BACKLOG[0], expect=409)
    await flows.not_now(owner, BACKLOG[0], expect=409)


# --- The unlock ---


@tuned()
async def test_the_rating_that_crosses_the_bar_says_so_once(owner):
    """One line, on the screen of the act that earned it, and never again.

    Discovery is already lit here: five films across two bands cleared *forming* a rating
    ago, and the pre-gate Watchlist read armed it. So this landing names the one bar it
    actually crossed, which is the whole point of naming any.
    """
    await build_ordering(owner, WESTERNS, band=4.0)
    await build_ordering(owner, HORRORS[:2], band=2.0)
    assert (await flows.tier(owner))["unlocked"] is False

    landed = await rate(owner, HORRORS[2], 2.0)

    assert landed["unlocked"] == ["watchlist"]
    again = await rate(owner, BACKLOG[0], 1.0)
    assert again["unlocked"] == [], "a later landing re-announces nothing"


@tuned()
async def test_the_dot_shows_once_and_clears_on_the_first_visit(owner):
    assert (await flows.unlocks(owner))["watchlist"] is False

    await make_ready(owner)

    assert (await flows.unlocks(owner))["watchlist"] is True
    await flows.tier(owner)
    assert (await flows.unlocks(owner))["watchlist"] is False
    await flows.tier(owner)
    assert (await flows.unlocks(owner))["watchlist"] is False, "the dot never returns"


@tuned()
async def test_arming_a_dot_that_is_already_lit_says_nothing_and_raises_nothing(
    owner, db, settings
):
    """Every surface that could be first to notice a crossing arms, so re-arming is ordinary.

    The nav re-asks on every navigation, the Watchlist arms on its own read, a rating arms
    as it lands, and the import worker arms as it finishes. Only the caller that actually
    lit a dot may say so - that is what puts the line on one screen and nowhere else - and
    none of the others may fail.

    The insert behind this is an upsert precisely so that two of them *arriving together*
    is a no-op for the loser rather than a unique-constraint 500. That concurrent case is
    deliberately not driven here: two open transactions racing one key block on each other,
    which in a single-threaded test harness is a wedge rather than a proof (testing.md
    wants no flakiness). What is pinned is the property the upsert exists for.
    """
    account = uuid.UUID(await flows.account_id(owner))
    await make_ready(owner)
    # Back to the instant before any surface had noticed, which is the moment this is
    # about: rating the films that crossed the bar is itself one of the surfaces that arms.
    async with db.sessions() as session:
        await session.execute(
            text("DELETE FROM unlock_marks WHERE account_id = :id"), {"id": account}
        )
        await session.commit()

    async with db.sessions() as session:
        lit = await unlocks.arm(session, account, settings)
        await session.commit()
    async with db.sessions() as session:
        again = await unlocks.arm(session, account, settings)
        await session.commit()

    assert lit == {Unlock.discovery, Unlock.watchlist}, "the first caller gets to say so"
    assert again == set(), "and every later one says nothing"
    assert await flows.unlocks(owner) == {"discovery": True, "watchlist": True}


@tuned()
async def test_an_account_that_crosses_both_bars_at_once_earns_both_dots(owner):
    """Which is what any real seed import does (onboarding-and-import.md)."""
    await make_ready(owner)

    dots = await flows.unlocks(owner)

    assert dots == {"discovery": True, "watchlist": True}


@tuned()
async def test_the_discovery_dot_clears_on_its_own_screen(owner):
    """Each dot lives on the one surface it points at, and clears there and nowhere else."""
    await make_ready(owner)

    await flows.tier(owner)

    assert (await flows.unlocks(owner)) == {"discovery": True, "watchlist": False}
    await flows.seen_discovery(owner)
    assert (await flows.unlocks(owner)) == {"discovery": False, "watchlist": False}


@tuned(readiness_forming_bands=3, **PATIENT)
async def test_a_re_rate_that_crosses_the_bar_still_opens_on_a_real_tier(owner):
    """The crossing is a change to what the tier is computed from, and the only one.

    A re-rate moves no film into the account and logs no watch, so the fit stands where
    the last pre-gate read stamped it - its retrain is still queued - and so does the
    watch clock. Nothing but the unlock itself can make the next read do its work, which
    is what makes this the case that catches an unlock armed behind the tier's back.
    """
    await fill(owner, BACKLOG[:5])
    await build_ordering(owner, WESTERNS, band=4.0)
    await build_ordering(owner, HORRORS, band=2.0)
    assert (await flows.tier(owner))["unlocked"] is False, "six films, but only two bands"

    await flows.re_rate(owner, HORRORS[2], 1.0)

    payload = await flows.tier(owner)
    assert payload["unlocked"] is True
    assert set(tier_ids(payload)) == {film.tmdb_id for film in BACKLOG[:5]}


@tuned(**PATIENT)
async def test_the_tier_is_there_the_moment_it_unlocks(owner):
    """The one announced moment must not open onto an empty screen.

    The pre-gate read stamps the fingerprint the refresh is gated on, and the rating that
    crosses the bar moves neither the fit - its retrain is still queued - nor the watch
    clock. The unlock itself has to be what makes the next read do its work.
    """
    await fill(owner, BACKLOG[:5])
    await build_ordering(owner, WESTERNS, band=4.0)
    await build_ordering(owner, HORRORS[:2], band=2.0)
    assert (await flows.tier(owner))["unlocked"] is False

    await rate(owner, HORRORS[2], 2.0)

    payload = await flows.tier(owner)
    assert payload["unlocked"] is True
    assert set(tier_ids(payload)) == {film.tmdb_id for film in BACKLOG[:5]}


# --- The shape ---


@tuned(**PATIENT)
async def test_the_tier_is_thirty_films_with_five_up_next(owner):
    await fill(owner, BACKLOG[:35])
    await make_ready(owner)

    payload = await flows.tier(owner)

    assert len(payload["up_next"]) == 5
    assert len(payload["pool"]) == 25
    assert tier_ids(payload) == [film.tmdb_id for film in BACKLOG[:30]]
    assert [film["tmdb_id"] for film in (await flows.backlog(owner))["films"]] == [
        film.tmdb_id for film in reversed(BACKLOG[30:35])
    ], "the backlog below the tier lists what the tier is not already showing"
    assert_nothing_rating_shaped(payload, "the ranked tier")


@tuned(**PATIENT)
async def test_a_tier_smaller_than_the_zone_is_all_up_next(owner):
    await fill(owner, BACKLOG[:3])
    await make_ready(owner)

    payload = await flows.tier(owner)

    assert tier_ids(payload) == [film.tmdb_id for film in BACKLOG[:3]]
    assert payload["pool"] == []


@tuned(**PATIENT)
async def test_the_tier_draws_only_from_the_backlog(owner):
    """Rated films and watched-unrated ones are not candidates; discovery stays quarantined."""
    await fill(owner, BACKLOG[:3])
    await make_ready(owner)
    await log_watches(owner, SPARE[:2])

    seated = tier_ids(await flows.tier(owner))

    assert seated == [film.tmdb_id for film in BACKLOG[:3]]
    assert not {film.tmdb_id for film in RATED + SPARE[:2]} & set(seated)


@tuned(**PATIENT)
async def test_one_owners_tier_is_never_anothers(owner, other_owner):
    await fill(owner, BACKLOG[:3])
    await make_ready(owner)
    await fill(other_owner, RIVALS[:2])
    await make_ready(other_owner)

    assert tier_ids(await flows.tier(owner)) == [film.tmdb_id for film in BACKLOG[:3]]
    assert tier_ids(await flows.tier(other_owner)) == [film.tmdb_id for film in RIVALS[:2]]


@tuned(**PATIENT)
async def test_the_tier_needs_a_logged_in_account(client):
    assert (await client.get("/api/watchlist/tier")).status_code == 401


# --- What the fit actually thinks ---


@tuned(**PATIENT)
async def test_the_tier_puts_what_the_owner_likes_first(owner, run_jobs):
    """The one test that reads the engine's opinion, and it reads it as an order.

    Every Western outranks every Horror in the library, so the fit prefers Westerns and
    the up-next zone fills with them - which is the whole of what the tier claims.
    """
    await fill(owner, BACKLOG[:20] + RIVALS[:5])
    await make_ready(owner, run_jobs)

    payload = await flows.tier(owner)

    assert [film["tmdb_id"] for film in payload["up_next"]] == [film.tmdb_id for film in RIVALS[:5]]


# --- Session boundaries ---


@tuned(**PATIENT)
async def test_reading_the_screen_again_never_moves_it(owner, run_jobs):
    """Maintenance runs at a boundary, not at a request: a re-read is not a new boundary."""
    await fill(owner, BACKLOG[:35])
    await make_ready(owner, run_jobs)

    first = tier_ids(await flows.tier(owner))
    again = tier_ids(await flows.tier(owner))
    filtered = tier_ids(await flows.tier(owner))

    assert first == again == filtered


@tuned(**PATIENT, tier_swap_budget=1)
async def test_a_reload_inside_a_session_is_not_a_boundary(owner, run_jobs):
    """The screen reloading after the owner's own action shows that action and nothing else.

    A watch moves the clock, and the read that follows it on the same screen is a reload
    rather than an arrival: the engine's next swap waits for the owner's next visit, so
    the list never moves under the cursor (watchlist.md).
    """
    await with_challengers_waiting(owner, run_jobs)
    after_one = tier_ids(await flows.tier(owner))
    assert len(rivals_in(after_one)) == 1

    await log_watches(owner, SPARE[:1])

    assert tier_ids(await flows.tier(owner, boundary=False)) == after_one
    assert len(rivals_in(tier_ids(await flows.tier(owner)))) == 2, "the next visit is"


@tuned(**PATIENT, tier_swap_budget=1)
async def test_a_shift_the_engine_wants_rolls_in_over_boundaries(owner, run_jobs):
    """A tier that turns over all at once is a tier the owner no longer recognises.

    Five films the fit prefers become eligible in one moment, and one seat changes hands
    per boundary. Re-reading the screen is not another boundary; the next visit after a
    watch is.
    """
    await with_challengers_waiting(owner, run_jobs)

    after_one = tier_ids(await flows.tier(owner))
    assert len(rivals_in(after_one)) == 1, "one swap, and only one"
    assert tier_ids(await flows.tier(owner)) == after_one, "a re-read is not a boundary"

    await log_watches(owner, SPARE[:1])

    assert len(rivals_in(tier_ids(await flows.tier(owner)))) == 2


@tuned(
    tier_staleness_watches=100,
    tier_enter_cooldown=0,
    tier_reentry_cooldown=0,
    tier_hysteresis=1.0,
    tier_swap_budget=30,
)
async def test_hysteresis_holds_a_seat_against_a_wobble(owner, run_jobs):
    """A margin as wide as the whole spread of scores swallows any shift there could be.

    The same five challengers that take a seat apiece under a zero margin take none here,
    which is the entire point of the mechanism: membership answers to real differences,
    never to a score that jiggled past its neighbour.
    """
    await with_challengers_waiting(owner, run_jobs)

    assert rivals_in(tier_ids(await flows.tier(owner))) == []


@tuned(
    tier_staleness_watches=100,
    tier_enter_cooldown=4,
    tier_reentry_cooldown=0,
    tier_hysteresis=0.0,
    tier_swap_budget=30,
)
async def test_a_fresh_seat_is_not_dropped_the_moment_it_is_taken(owner, run_jobs):
    """The enter cooldown: no immediate drops, however much the engine wants the swap."""
    await with_challengers_waiting(owner, run_jobs)

    assert rivals_in(tier_ids(await flows.tier(owner))) == [], "everything is newly seated"

    await log_watches(owner, SPARE[:4])

    assert rivals_in(tier_ids(await flows.tier(owner))) != []


# --- Vacancies and arrivals ---


@tuned(**PATIENT, tier_swap_budget=0)
async def test_a_watched_film_frees_its_seat_at_once(owner):
    """Refilling a seat is not churn, so it does not wait behind the swap budget.

    Read as the screen's own reload rather than as a boundary, so the refill is shown to
    be the watch's doing and not the maintenance the next visit would have run anyway.
    """
    await fill(owner, BACKLOG[:31])
    await make_ready(owner)
    seated = tier_ids(await flows.tier(owner))
    assert BACKLOG[30].tmdb_id not in seated

    await flows.mark_watched(owner, BACKLOG[0])

    after = tier_ids(await flows.tier(owner, boundary=False))
    assert BACKLOG[0].tmdb_id not in after
    assert BACKLOG[30].tmdb_id in after
    assert len(after) == 30


@tuned(**PATIENT, tier_swap_budget=0)
async def test_a_film_removed_from_the_backlog_frees_its_seat_at_once(owner):
    await fill(owner, BACKLOG[:31])
    await make_ready(owner)

    await flows.remove_from_backlog(owner, BACKLOG[0])

    after = tier_ids(await flows.tier(owner))
    assert BACKLOG[0].tmdb_id not in after
    assert BACKLOG[30].tmdb_id in after


@tuned(**PATIENT, tier_swap_budget=0)
async def test_a_film_just_added_enters_at_once_if_it_scores_in(owner, run_jobs):
    """The owner told the app something, and reacting to it is the point.

    The swap budget is zero, so nothing the engine wanted could have moved: the seat this
    film takes is the newly-backlogged exception and nothing else.
    """
    await fill(owner, BACKLOG[:30])
    await make_ready(owner, run_jobs)
    assert len(tier_ids(await flows.tier(owner))) == 30

    await add_to_backlog(owner, RIVALS[0])

    assert RIVALS[0].tmdb_id in tier_ids(await flows.tier(owner))


# --- Staleness ---


@tuned(
    tier_staleness_watches=3,
    tier_enter_cooldown=0,
    tier_reentry_cooldown=4,
    tier_hysteresis=0.0,
)
async def test_a_film_passed_over_often_enough_rotates_out_and_comes_back(owner):
    """Passed over, not judged: the score is untouched, so a strong film returns."""
    await fill(owner, BACKLOG[:3])
    await make_ready(owner)
    assert len(tier_ids(await flows.tier(owner))) == 3

    await log_watches(owner, SPARE[:2])
    await add_to_backlog(owner, BACKLOG[3])
    await log_watches(owner, SPARE[2:3])

    rotated = await flows.tier(owner)
    assert tier_ids(rotated) == [BACKLOG[3].tmdb_id], "the newcomer has not been passed over"
    assert [film["tmdb_id"] for film in (await flows.backlog(owner))["films"]] == [
        film.tmdb_id for film in reversed(BACKLOG[:3])
    ], "rotated out of the tier, still in the backlog"

    await log_watches(owner, SPARE[3:7])

    returned = tier_ids(await flows.tier(owner))
    assert set(returned) == {film.tmdb_id for film in BACKLOG[:3]}, "their scores were untouched"
    assert BACKLOG[3].tmdb_id not in returned, "and by now it has sat there just as long"


@tuned(tier_staleness_watches=1, tier_enter_cooldown=0, tier_reentry_cooldown=1)
async def test_a_dormant_account_never_shuffles_itself(owner, db, run_jobs):
    """Every measure is the watch clock, so an account nobody is using does not move.

    The staleness threshold is one watch, which would rotate the whole tier out on the
    next one - and the account is read four times over without a single film moving,
    because no watch happened. Then the fit lands, which is a boundary that does weigh
    every seat for staleness, and still nothing moves: the clock has not. There is no
    calendar anywhere in this to freeze.
    """
    await fill(owner, BACKLOG[:5])
    await make_ready(owner)
    before = tier_ids(await flows.tier(owner))
    still = await watch_clock(db, await flows.account_id(owner))

    for _ in range(3):
        assert tier_ids(await flows.tier(owner)) == before

    await run_jobs()

    assert tier_ids(await flows.tier(owner)) == before, "a boundary with nothing stale on it"
    assert await watch_clock(db, await flows.account_id(owner)) == still


# --- The overrides ---


@tuned(**PATIENT, tier_swap_budget=0)
async def test_a_pin_holds_a_film_at_the_top_of_the_up_next_zone(owner, run_jobs):
    await fill(owner, BACKLOG[:20] + RIVALS[:5])
    await make_ready(owner, run_jobs)
    assert BACKLOG[19].tmdb_id not in [
        film["tmdb_id"] for film in (await flows.tier(owner))["up_next"]
    ]

    await flows.pin(owner, BACKLOG[19])

    payload = await flows.tier(owner)
    assert payload["up_next"][0]["tmdb_id"] == BACKLOG[19].tmdb_id
    assert payload["up_next"][0]["pinned"] is True
    assert all(not film["pinned"] for film in payload["up_next"][1:])


@tuned(**PATIENT)
async def test_pins_stack_in_pin_order_up_to_the_zone_size(owner):
    await fill(owner, BACKLOG[:10])
    await make_ready(owner)

    for film in BACKLOG[5:10]:
        await flows.pin(owner, film)
    await flows.pin(owner, BACKLOG[0], expect=409)

    payload = await flows.tier(owner)
    assert [film["tmdb_id"] for film in payload["up_next"]] == [
        film.tmdb_id for film in BACKLOG[5:10]
    ]


@tuned(**PATIENT, tier_swap_budget=0)
async def test_a_pin_takes_its_seat_from_the_cap_not_from_the_zone(owner):
    """Thirty is thirty. A pin on a film the tier did not hold costs the weakest its seat."""
    await fill(owner, BACKLOG[:31])
    await make_ready(owner)
    assert BACKLOG[30].tmdb_id not in tier_ids(await flows.tier(owner))

    await flows.pin(owner, BACKLOG[30])

    payload = await flows.tier(owner)
    assert len(tier_ids(payload)) == 30
    assert payload["up_next"][0]["tmdb_id"] == BACKLOG[30].tmdb_id
    assert BACKLOG[29].tmdb_id not in tier_ids(payload), "the weakest gave way"

    await flows.remove_from_backlog(owner, BACKLOG[30])

    assert BACKLOG[29].tmdb_id in tier_ids(await flows.tier(owner)), "crowded out, not cooled off"


@tuned(tier_staleness_watches=1, tier_enter_cooldown=0, tier_reentry_cooldown=9)
async def test_a_pin_is_immune_to_automatic_maintenance(owner):
    """Rotation is the engine's business, and a pin is the owner overruling it."""
    await fill(owner, BACKLOG[:4])
    await make_ready(owner)
    await flows.pin(owner, BACKLOG[0])

    await log_watches(owner, SPARE[:2])

    assert tier_ids(await flows.tier(owner)) == [BACKLOG[0].tmdb_id]


@tuned(**PATIENT)
async def test_a_pinned_film_leaves_by_watch_unpin_or_removal(owner, db):
    await fill(owner, BACKLOG[:3])
    await make_ready(owner)
    for film in BACKLOG[:3]:
        await flows.pin(owner, film)

    await flows.unpin(owner, BACKLOG[0])
    await flows.remove_from_backlog(owner, BACKLOG[1])
    await flows.mark_watched(owner, BACKLOG[2])

    payload = await flows.tier(owner)
    assert [film["tmdb_id"] for film in payload["up_next"]] == [BACKLOG[0].tmdb_id]
    assert payload["up_next"][0]["pinned"] is False, "unpinned, and the engine kept it anyway"
    standings = await watch_standings(db, await flows.account_id(owner))
    assert standings[BACKLOG[2].tmdb_id] == "pinned"


@tuned(**PATIENT, tier_swap_budget=0)
async def test_a_veto_bars_a_film_until_it_is_lifted(owner):
    """Never distaste: the film keeps its place in the backlog and its score is untouched."""
    await fill(owner, BACKLOG[:5])
    await make_ready(owner)

    await flows.veto(owner, BACKLOG[0])

    vetoed = await flows.tier(owner)
    assert tier_ids(vetoed) == [film.tmdb_id for film in BACKLOG[1:5]]
    assert [film["tmdb_id"] for film in vetoed["vetoed"]] == [BACKLOG[0].tmdb_id]
    assert BACKLOG[0].tmdb_id in [
        film["tmdb_id"] for film in (await flows.backlog(owner))["films"]
    ], "barred from the tier, still in the backlog"

    await flows.lift_veto(owner, BACKLOG[0])

    lifted = await flows.tier(owner)
    assert lifted["vetoed"] == []
    assert tier_ids(lifted) == [film.tmdb_id for film in BACKLOG[:5]], "the standing it had"


@tuned(**PATIENT, tier_swap_budget=0)
async def test_the_seat_a_veto_empties_refills_at_once(owner):
    """Refilling a seat is not churn, so it does not wait behind the swap budget."""
    await fill(owner, BACKLOG[:31])
    await make_ready(owner)
    assert BACKLOG[30].tmdb_id not in tier_ids(await flows.tier(owner))

    await flows.veto(owner, BACKLOG[0])

    after = tier_ids(await flows.tier(owner))
    assert BACKLOG[0].tmdb_id not in after
    assert BACKLOG[30].tmdb_id in after
    assert len(after) == 30


@tuned(
    tier_staleness_watches=100,
    tier_enter_cooldown=0,
    tier_reentry_cooldown=3,
    tier_hysteresis=0.0,
    tier_swap_budget=0,
)
async def test_not_now_rotates_a_film_out_with_the_standard_cooldown(owner):
    """The mood-level version of a veto: it comes back on its own, in watches."""
    await fill(owner, BACKLOG[:3])
    await make_ready(owner)

    await flows.not_now(owner, BACKLOG[0])

    assert tier_ids(await flows.tier(owner)) == [film.tmdb_id for film in BACKLOG[1:3]]

    await log_watches(owner, SPARE[:5])

    assert BACKLOG[0].tmdb_id in tier_ids(await flows.tier(owner))


@tuned(
    tier_staleness_watches=100,
    tier_enter_cooldown=0,
    tier_reentry_cooldown=3,
    tier_hysteresis=0.0,
    tier_swap_budget=0,
)
async def test_an_override_is_never_queued_behind_the_swap_budget(owner):
    """The budget is zero, so every one of these moved on the owner's word alone."""
    await fill(owner, BACKLOG[:5])
    await make_ready(owner)

    await flows.pin(owner, BACKLOG[4])
    await flows.veto(owner, BACKLOG[0])
    await flows.not_now(owner, BACKLOG[1])

    payload = await flows.tier(owner)
    assert payload["up_next"][0]["tmdb_id"] == BACKLOG[4].tmdb_id
    assert tier_ids(payload) == [BACKLOG[4].tmdb_id, BACKLOG[2].tmdb_id, BACKLOG[3].tmdb_id]


@tuned(**PATIENT)
async def test_an_override_needs_a_film_in_the_backlog(owner):
    await fill(owner, BACKLOG[:3])
    await make_ready(owner)

    await flows.pin(owner, BACKLOG[10], expect=404)
    await flows.veto(owner, RATED[0], expect=404)


# --- The profile firewall ---


@tuned(tier_staleness_watches=1, tier_enter_cooldown=0, tier_reentry_cooldown=1)
async def test_no_tier_signal_ever_reaches_the_taste_profile(owner, db, run_jobs):
    """ADR 0012: pins, vetoes, not-nows and rotations are queue management, never evidence.

    Only the ordering trains taste, so every artifact the profile is made of has to come
    out of this untouched - and so does the log, which is where evidence would have to
    have been written to reach them.
    """
    await fill(owner, BACKLOG[:6])
    await make_ready(owner, run_jobs)
    account = await flows.account_id(owner)
    before = (
        await weight_vector(db, account),
        await exemplars(db, account),
        await taste_metrics(db, account),
        await comparison_log(db, account),
    )

    await flows.pin(owner, BACKLOG[0])
    await flows.veto(owner, BACKLOG[1])
    await flows.not_now(owner, BACKLOG[2])
    await log_watches(owner, SPARE[:2])
    await flows.tier(owner)

    assert (
        await weight_vector(db, account),
        await exemplars(db, account),
        await taste_metrics(db, account),
        await comparison_log(db, account),
    ) == before


@tuned(**PATIENT)
async def test_nothing_rating_shaped_reaches_the_tier_or_the_backlog(owner, run_jobs):
    """ADR 0005: position is the entire public statement about an unwatched film."""
    await fill(owner, BACKLOG[:10] + RIVALS[:2])
    await make_ready(owner, run_jobs)
    await flows.veto(owner, BACKLOG[0])
    await flows.pin(owner, BACKLOG[1])

    assert_nothing_rating_shaped(await flows.tier(owner), "the tier")
    assert_nothing_rating_shaped(await flows.backlog(owner), "the backlog")
