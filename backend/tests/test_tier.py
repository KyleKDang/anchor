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

import pytest

import flows
from faketmdb import FilmFixture
from flows import (
    add_to_backlog,
    build_ordering,
    designate,
    log_watches,
    place,
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
    "readiness_forming_bands": 1,
    "readiness_ready_films": 6,
    "readiness_ready_settled_share": 0.5,
    "readiness_ready_comparisons_per_film": 1.0,
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
    await build_ordering(owner, RATED)
    await designate(owner, 4.0, RATED[0])
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
        "settled_share",
        "comparisons_per_film",
    }


async def test_progress_climbs_as_the_evidence_does(owner):
    await build_ordering(owner, RATED[:3])

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
async def test_the_placement_that_crosses_the_bar_says_so_once(owner):
    await build_ordering(owner, RATED[:5])
    await designate(owner, 4.0, RATED[0])
    assert (await flows.tier(owner))["unlocked"] is False

    landed, _ = await place(owner, RATED[5], "b")

    assert landed["unlocked"] is True
    resumed = await flows.begin(owner, RATED[5])
    assert resumed["unlocked"] is False, "resuming a landed placement re-announces nothing"


@tuned(readiness_ready_comparisons_per_film=1.5)
async def test_a_keep_comparing_answer_that_crosses_the_bar_says_so_too(owner):
    """The line goes on whichever done screen earned it, and keep-comparing has one.

    A keep-comparing answer is a comparison like any other, so it can be the evidence that
    crosses the bar - and the screen it lands on is a placement-done screen (surfacing.md).
    The bar here is set just above what the six-film library already has, so the one extra
    comparison is exactly what carries it over.
    """
    await build_ordering(owner, RATED)
    await designate(owner, 4.0, RATED[0])
    assert (await flows.tier(owner))["unlocked"] is False

    step = await flows.keep_comparing(owner, RATED[3])
    assert step["kind"] == "comparison"
    landed = await flows.answer(owner, RATED[3], step["b"]["tmdb_id"], "b")

    assert landed["unlocked"] is True
    assert (await flows.tier(owner))["unlocked"] is True


@tuned()
async def test_the_dot_shows_once_and_clears_on_the_first_visit(owner):
    assert (await flows.unlocks(owner))["watchlist"] is False

    await make_ready(owner)

    assert (await flows.unlocks(owner))["watchlist"] is True
    await flows.tier(owner)
    assert (await flows.unlocks(owner))["watchlist"] is False
    await flows.tier(owner)
    assert (await flows.unlocks(owner))["watchlist"] is False, "the dot never returns"


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
async def test_a_shift_the_engine_wants_rolls_in_over_boundaries(owner, run_jobs):
    """A tier that turns over all at once is a tier the owner no longer recognises.

    Five films the fit prefers become eligible in one moment, and one seat changes hands
    per boundary. Re-reading the screen is not another boundary; logging a watch is.
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
    """Refilling a seat is not churn, so it does not wait behind the swap budget."""
    await fill(owner, BACKLOG[:31])
    await make_ready(owner)
    seated = tier_ids(await flows.tier(owner))
    assert BACKLOG[30].tmdb_id not in seated

    await flows.mark_watched(owner, BACKLOG[0])

    after = tier_ids(await flows.tier(owner))
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
async def test_a_dormant_account_never_shuffles_itself(owner, db):
    """Every measure is the watch clock, so an account nobody is using does not move.

    The staleness threshold is one watch, which would rotate the whole tier out on the
    next one - and the account is read four times over without a single film moving,
    because no watch happened. There is no calendar anywhere in this to freeze.
    """
    await fill(owner, BACKLOG[:5])
    await make_ready(owner)
    before = tier_ids(await flows.tier(owner))
    still = await watch_clock(db, await flows.account_id(owner))

    for _ in range(3):
        assert tier_ids(await flows.tier(owner)) == before

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
