"""Acting on the discovery feed, and what the feed's economy costs to keep running.

Where test_discovery.py asks what reaches the shelf, this asks what happens when the
owner touches it: the three actions, the instant backfill behind them, the visit gating
that decides whether anything is ever bought, rotation, and the fresh markers.

The claims here are mostly about money and about restraint, and the suite is weighted
that way. A feed that spends on an owner who never opens it, re-suggests a film they said
no to, learns something from a single dismissal, or quietly rewrites the shelf while they
are reading it would all pass a happy-path suite and would all be the bug.

Every clock in here is the owner's own activity. There is no calendar time anywhere: the
refresh counter moves when the owner arrives at the feed, and a test that needs it to
move visits the feed, because that is the only thing that ever moves it (testing.md).
"""

import uuid

import pytest

from anchor import llm
from faketmdb import FilmFixture
from flows import (
    accept,
    account_id,
    add_to_backlog,
    build_ordering,
    discovery,
    dismiss_suggestion,
    dismissed,
    film_page,
    lift_dismissal,
    mark_anchor,
    queue_of,
    rate,
    rated,
    seen_it,
    shelf,
    thumb_down,
    tier,
    tier_ids,
    unlocks,
)
from invariants import (
    assert_shelf_stands_on_verdicts,
    cooldowns,
    dismissal_rows,
    feed_state,
    prose_versions,
    spend_ledger,
    suggestion_clocks,
    verdicts,
    watch_origins,
)

PIPELINE = dict(
    readiness_forming_films=3,
    readiness_forming_bands=1,
    readiness_ready_films=5,
    prose_placements_trigger=2,
    discovery_shelf=3,
    discovery_shortlist=8,
    discovery_rerank_window=10,
)
"""Small bars and a small pipeline: five placements and a call or two rather than fifty,
saying something that is true at any size. The dimensions are spec; the numbers are
tuning. One rerank window, so a scripted ranking is the whole ranking."""

pytestmark = pytest.mark.settings(**PIPELINE)


def tuned(**overrides):
    """The standard pipeline with this test's own numbers on top, as one settings mark.

    One mark rather than two stacked, because only the closest one is read and the
    pipeline has to travel with whatever a test is actually tuning.
    """
    return pytest.mark.settings(**{**PIPELINE, **overrides})


RATED = (
    FilmFixture(4000, "Once Upon a Time in the West", genres=("Western",), directors=("Leone",)),
    FilmFixture(4001, "The Good, the Bad and the Ugly", genres=("Western",), directors=("Leone",)),
    FilmFixture(4002, "A Fistful of Dollars", genres=("Western",), directors=("Leone",)),
    FilmFixture(4003, "Saw", genres=("Horror",), directors=("Wan",)),
    FilmFixture(4004, "Saw II", genres=("Horror",), directors=("Bousman",)),
)
"""The owner's library: westerns at the top, horror at the bottom, so the fit has a shape."""

CANDIDATES = tuple(
    FilmFixture(4100 + n, f"Corbucci {n:02d}", genres=("Western",), directors=("Corbucci",))
    for n in range(12)
)
"""Untracked films TMDB offers back - more than the shelf holds, so there is always a
next-ranked candidate for a backfill to reach for."""

CATALOG = RATED + CANDIDATES


@pytest.fixture(autouse=True)
def stocked(tmdb):
    return tmdb.with_films(*CATALOG).with_neighbours(RATED[0].tmdb_id, *CANDIDATES)


def ranked(*films, fit="strong_fit"):
    """One scripted rerank answer: these films, in this order, all at this bucket.

    Handed the whole candidate set on purpose. The seam drops anything that was not
    offered, so the same answer is a correct answer to every window and no test has to
    know which films the prefilter put in which one.
    """
    return {
        "ranked": [
            {
                "tmdb_id": film.tmdb_id,
                "fit": fit,
                "explanation": f"Because you loved {film.title}.",
            }
            for film in films
        ]
    }


def ids(films):
    return [film["tmdb_id"] for film in films]


def _origins_of(stamped, films):
    """Just these films' provenance stamps, in the order the films were named.

    The library the account was built with logged watches of its own, so the table is
    never empty by the time an action runs. What is under test is where a *candidate*
    came from, so the rest is filtered out rather than asserted around.
    """
    origins = dict(stamped)
    return [(film.tmdb_id, origins[film.tmdb_id]) for film in films if film.tmdb_id in origins]


async def _reranks(db, account_id):
    """How many shortlists this account has ever paid to have reranked."""
    return len([row for row in await spend_ledger(db, account_id) if row[1] == "rerank_candidates"])


async def rating_films(client, run_jobs):
    """An account at *forming* with its first prose written: the state discovery needs."""
    await build_ordering(client, RATED[:3], band=4.0)
    await build_ordering(client, RATED[3:], band=2.0)
    await mark_anchor(client, RATED[0])
    await mark_anchor(client, RATED[4])
    await run_jobs()
    return uuid.UUID(await account_id(client))


async def turn_down(client, count):
    """Turn down ``count`` suggestions, whichever ones the shelf is offering.

    Worked through the action responses rather than by re-reading the screen, because a
    read is a session boundary and a boundary advances the refresh counter: an owner
    clearing a few cards in one sitting does not refresh the feed between them, and a test
    that did would rotate the shelf out from under its own pile.
    """
    standing = await shelf(client)
    for _ in range(count):
        assert standing, "the shelf ran out before the pile was made"
        standing = (await dismiss_suggestion(client, standing[0]["tmdb_id"]))["films"]


async def stocked_shelf(client, run_jobs, provider, *films):
    """Get an account to a full shelf of ``films``, and hand back what is on it.

    Two arrivals, because nothing on the request path may wait on a provider: the first
    is what queues the restock, and the second is the session boundary at which the shelf
    is rebuilt from the verdicts the restock bought. It is the honest shape of the
    feature, so the tests are written in it rather than around it.
    """
    provider.will_say(**ranked(*films))
    await rating_films(client, run_jobs)
    await discovery(client)
    await run_jobs()
    return await shelf(client)


# --- Accept ---


async def test_accept_puts_the_film_in_the_backlog_and_takes_the_card_away(
    owner, run_jobs, provider
):
    """The one thing accept does, and it does it the moment the owner asks."""
    standing = await stocked_shelf(owner, run_jobs, provider, *CANDIDATES)
    taken = standing[0]["tmdb_id"]

    left = await accept(owner, CANDIDATES[0])

    assert taken not in ids(left["films"])
    # Read off the film's own page rather than the backlog *list*, which is the half of
    # the backlog sitting below the ranked tier - and this film may well have scored a
    # seat on the way in, which is the next test's subject.
    assert (await film_page(owner, CANDIDATES[0]))["state"] == "backlog"


async def test_accept_feeds_nothing(owner, run_jobs, provider, db):
    """Anticipation is not judgment (ADR 0006): accepting teaches the profile nothing.

    Asserted as the absence of the one channel a queue action could reach the profile
    through - the dismissal record - and as the prompt a regeneration would be shown. An
    accept that quietly filed itself as evidence would be a recommender training on its
    own suggestions, which is the loop ADR 0006 rejects outright.
    """
    account = uuid.UUID(await account_id(owner))
    await stocked_shelf(owner, run_jobs, provider, *CANDIDATES)
    before = await prose_versions(db, account)

    for film in CANDIDATES[:3]:
        await accept(owner, film)
    await run_jobs()

    assert await dismissal_rows(db, account) == []
    assert await prose_versions(db, account) == before


@tuned(tier_hysteresis=0.0, tier_enter_cooldown=0)
async def test_a_strong_accept_can_reach_the_ranked_tier(owner, run_jobs, provider):
    """Quarantine means no bypass of the engine, not artificial delay (discovery.md).

    The film goes through the newly-backlogged exception on the scorer's own terms, the
    same as any hand-added film - which is why this is asserted through the tier rather
    than through anything discovery owns. The feed never writes a tier seat.
    """
    await stocked_shelf(owner, run_jobs, provider, *CANDIDATES)

    await accept(owner, CANDIDATES[0])

    assert CANDIDATES[0].tmdb_id in tier_ids(await tier(owner, boundary=False))


async def test_an_accepted_film_carries_its_origin_to_the_watch_it_earns(
    owner, run_jobs, provider, db
):
    """The one thing an accept leaves behind, and it is measurement only (evaluation.md).

    The stamp is capture-or-lose-forever: by the time the film is watched the card is
    long gone, so the provenance has to ride along from the accept. Nothing branches on
    it anywhere - it exists so the accept rate can be counted at all.
    """
    account = uuid.UUID(await account_id(owner))
    await stocked_shelf(owner, run_jobs, provider, *CANDIDATES)
    await accept(owner, CANDIDATES[0])
    await add_to_backlog(owner, CANDIDATES[1])

    await owner.post(f"/api/films/{CANDIDATES[0].tmdb_id}/watched", json={"rate": "later"})
    await owner.post(f"/api/films/{CANDIDATES[1].tmdb_id}/watched", json={"rate": "later"})

    assert _origins_of(await watch_origins(db, account), CANDIDATES[:2]) == [
        (CANDIDATES[0].tmdb_id, "discovery_accept"),
        (CANDIDATES[1].tmdb_id, "hand_added"),
    ]


# --- Dismissal ---


async def test_a_dismissed_film_is_suppressed_and_kept_on_a_reviewable_list(
    owner, run_jobs, provider
):
    """Permanently until lifted, and visible: the dismissed list is behind the overflow."""
    await stocked_shelf(owner, run_jobs, provider, *CANDIDATES)

    left = await dismiss_suggestion(owner, CANDIDATES[0])

    assert CANDIDATES[0].tmdb_id not in ids(left["films"])
    assert [film["tmdb_id"] for film in await dismissed(owner)] == [CANDIDATES[0].tmdb_id]


async def test_a_dismissed_film_never_comes_back_to_the_shelf(owner, run_jobs, provider):
    """Not at the next boundary, not at the next restock, not through the backfill."""
    await stocked_shelf(owner, run_jobs, provider, *CANDIDATES)
    await dismiss_suggestion(owner, CANDIDATES[0])

    for _ in range(3):
        await discovery(owner)
        await run_jobs()

    assert CANDIDATES[0].tmdb_id not in ids(await shelf(owner))


async def test_a_single_dismissal_changes_nothing(owner, run_jobs, provider, db):
    """The magnitude guard, from the quiet side: one tap is far too noisy to be a fact.

    Nothing is bought and nothing is written into the profile. This is the failure ADR
    0006 spends most of its length guarding against, so it is asserted as the absence of
    a regeneration rather than as the wording of one.
    """
    account = uuid.UUID(await account_id(owner))
    await stocked_shelf(owner, run_jobs, provider, *CANDIDATES)
    before = await prose_versions(db, account)

    await dismiss_suggestion(owner, CANDIDATES[0])
    await run_jobs()

    assert await prose_versions(db, account) == before


@tuned(prose_dismissal_evidence_min=3)
async def test_accumulated_dismissals_reach_a_regeneration_as_pattern_evidence(
    owner, run_jobs, provider, db
):
    """Over the guard, the pile is evidence - and it is the pile that is shown, never one film.

    Asserted at the LLM operations seam, because the whole claim is about what a
    regeneration is allowed to read. The section is present, it names the films' shape
    rather than asking about any one of them, and the prompt says out loud that it is a
    pattern to read rather than a verdict.
    """
    account = uuid.UUID(await account_id(owner))
    await stocked_shelf(owner, run_jobs, provider, *CANDIDATES)
    await turn_down(owner, 3)

    # The regeneration is earned by rating, which is the only kind of thing that ever
    # earns one: a dismissal is read by a regeneration, never a reason to buy one.
    await rate(owner, RATED[0], 4.5)
    await rate(owner, RATED[1], 4.5)
    await run_jobs()

    assert len(await prose_versions(db, account)) > 1
    shown = provider.last_of(llm.PROSE_SYSTEM).prompt.user
    assert "Suggestions they turned down" in shown
    assert "Corbucci" in shown


@tuned(prose_dismissal_evidence_min=3)
async def test_a_constraint_outranks_any_dismissal_pattern(owner, run_jobs, provider, db):
    """The correction flow overrides a pattern durably (ADR 0006), and says so in the prompt.

    The owner's own words about themselves and the pile of refusals are both in the
    prompt, and the instruction that settles a fight between them is too: what they said
    outright is settled fact, and a pattern may never contradict it.
    """
    await stocked_shelf(owner, run_jobs, provider, *CANDIDATES)
    await turn_down(owner, 3)
    # The correction is itself a trigger, so this is the regeneration it earns.
    await thumb_down(owner, "You have no time for westerns")
    await run_jobs()

    asked = provider.last_of(llm.PROSE_SYSTEM).prompt
    assert "You have no time for westerns" in asked.user
    assert "It overrides anything" in asked.system
    assert "never contradict what they have said about themselves outright" in asked.system


async def test_lifting_a_dismissal_stamps_it_and_lets_the_film_back(owner, run_jobs, provider):
    """Changing your mind is itself evidence, so the row is stamped rather than deleted."""
    await stocked_shelf(owner, run_jobs, provider, *CANDIDATES)
    await dismiss_suggestion(owner, CANDIDATES[0])

    await lift_dismissal(owner, CANDIDATES[0])

    assert await dismissed(owner) == []
    assert CANDIDATES[0].tmdb_id in ids(await shelf(owner))


async def test_a_lifted_dismissal_is_kept_rather_than_deleted(owner, run_jobs, provider, db):
    account = uuid.UUID(await account_id(owner))
    await stocked_shelf(owner, run_jobs, provider, *CANDIDATES)
    await dismiss_suggestion(owner, CANDIDATES[0])

    await lift_dismissal(owner, CANDIDATES[0])

    assert await dismissal_rows(db, account) == [(CANDIDATES[0].tmdb_id, True)]


@tuned(prose_dismissal_evidence_min=3)
async def test_a_pile_of_dismissals_still_buys_nothing(owner, run_jobs, provider, db):
    """The pile is evidence, never a purchase order (ADR 0006).

    Dismissals are the weakest signal Anchor holds - taps made while clearing a queue -
    and the one thing they must not become is a spend path of their own. Well past the
    magnitude guard, an owner who does nothing but dismiss earns no regeneration at all;
    the pile waits for one that some real judgment of theirs pays for.
    """
    account = uuid.UUID(await account_id(owner))
    await stocked_shelf(owner, run_jobs, provider, *CANDIDATES)
    before = await prose_versions(db, account)

    await turn_down(owner, 6)
    await run_jobs()

    assert await prose_versions(db, account) == before


# --- Seen it ---


async def test_seen_it_converts_to_watched_unrated_with_a_rate_later_seat(
    owner, run_jobs, provider
):
    """Permanent dedupe and a seat: the resting state of any watched-unrated film."""
    await stocked_shelf(owner, run_jobs, provider, *CANDIDATES)

    left = await seen_it(owner, CANDIDATES[0])

    assert CANDIDATES[0].tmdb_id not in ids(left["films"])
    page = await film_page(owner, CANDIDATES[0])
    assert page["state"] == "watched_unrated"
    assert page["rate_later"] is True
    assert CANDIDATES[0].tmdb_id in queue_of(await rated(owner))


async def test_seen_it_offers_to_place_the_film_now(owner, run_jobs, provider):
    """An inline invite at a moment the owner triggered - the one thing seen-it says."""
    await stocked_shelf(owner, run_jobs, provider, *CANDIDATES)

    assert (await seen_it(owner, CANDIDATES[0]))["place_now"] is True


async def test_the_place_it_now_invite_is_skippable(owner, run_jobs, provider):
    """Skipping costs nothing: the film is already waiting in the rate-later queue.

    "Later" never becomes a promise (surfacing.md), so the test simply never answers the
    invite and asserts that the film is exactly where walking away should leave it.
    """
    await stocked_shelf(owner, run_jobs, provider, *CANDIDATES)

    await seen_it(owner, CANDIDATES[0])

    assert CANDIDATES[0].tmdb_id in queue_of(await rated(owner))


async def test_seen_it_is_recorded_separately_from_dismissal(owner, run_jobs, provider, db):
    """The split is what keeps the dismissal signal clean (discovery.md).

    With "already watched" filed elsewhere, a dismissal reliably means the pitch does not
    appeal - so a seen-it that leaked into the dismissal record would quietly poison the
    one signal ADR 0006 lets reach the profile.
    """
    account = uuid.UUID(await account_id(owner))
    await stocked_shelf(owner, run_jobs, provider, *CANDIDATES)

    await seen_it(owner, CANDIDATES[0])

    assert await dismissal_rows(db, account) == []
    assert await dismissed(owner) == []


async def test_a_seen_film_is_not_attributed_to_discovery(owner, run_jobs, provider, db):
    """The owner watched it before Anchor ever mentioned it, so the feed takes no credit.

    Stamping this one discovery-accept would inflate the accept-rate indicator with
    watches the feed did not cause (evaluation.md), which is the one thing the stamp
    exists to measure.
    """
    account = uuid.UUID(await account_id(owner))
    await stocked_shelf(owner, run_jobs, provider, *CANDIDATES)

    await seen_it(owner, CANDIDATES[0])

    assert _origins_of(await watch_origins(db, account), CANDIDATES[:1]) == [
        (CANDIDATES[0].tmdb_id, "hand_added")
    ]


# --- The instant backfill ---


async def test_an_action_backfills_the_slot_instantly_and_buys_nothing(
    owner, run_jobs, provider, db
):
    """The shelf is full again in the same response, from a verdict already paid for.

    Both halves matter. Instant, because a hole where a card was is the feed admitting it
    has to go and think; and free, because the next-ranked candidate is sitting in the
    cache with its sentence already written (discovery.md).
    """
    account = uuid.UUID(await account_id(owner))
    standing = await stocked_shelf(owner, run_jobs, provider, *CANDIDATES)
    spent = await spend_ledger(db, account)

    left = await accept(owner, CANDIDATES[0])

    assert len(left["films"]) == len(standing) == 3
    assert ids(left["films"])[:2] == ids(standing)[1:]
    assert ids(left["films"])[2] not in ids(standing)
    assert await spend_ledger(db, account) == spent
    await assert_shelf_stands_on_verdicts(db, account)


async def test_all_three_actions_backfill(owner, run_jobs, provider, db):
    """Whichever way a card leaves, the slot behind it closes at once."""
    account = uuid.UUID(await account_id(owner))
    await stocked_shelf(owner, run_jobs, provider, *CANDIDATES)

    for action, film in (
        (accept, CANDIDATES[0]),
        (dismiss_suggestion, CANDIDATES[1]),
        (seen_it, CANDIDATES[2]),
    ):
        assert len((await action(owner, film))["films"]) == 3

    await assert_shelf_stands_on_verdicts(db, account)


async def test_the_shelf_simply_runs_short_when_the_cache_is_empty(owner, run_jobs, provider, db):
    """No padding, ever. A backfill with nothing to reach for leaves the shelf shorter."""
    account = uuid.UUID(await account_id(owner))
    await stocked_shelf(owner, run_jobs, provider, *CANDIDATES[:3])

    left = await accept(owner, CANDIDATES[0])

    assert len(left["films"]) == 2
    await assert_shelf_stands_on_verdicts(db, account)


async def test_an_action_is_not_a_session_boundary(owner, run_jobs, provider, db):
    """Reloading after an action shows what the action did and nothing the engine did."""
    account = uuid.UUID(await account_id(owner))
    await stocked_shelf(owner, run_jobs, provider, *CANDIDATES)
    before = await feed_state(db, account)

    await accept(owner, CANDIDATES[0])
    await shelf(owner, boundary=False)

    assert (await feed_state(db, account))[0] == before[0]


# --- What the feed costs ---


async def test_an_owner_who_ignores_discovery_spends_nothing(owner, run_jobs, provider, db):
    """The whole of the feed's economy, stated as a ledger (discovery.md).

    The account does everything that would ordinarily schedule a restock - rates films,
    marks anchors, earns regeneration after regeneration - and never opens the feed. Not
    one candidate is reranked, however far the taste moves.
    """
    account = await rating_films(owner, run_jobs)
    for film in CANDIDATES[:4]:
        await add_to_backlog(owner, film)
        await rate(owner, film, 3.5)
    await run_jobs()

    bought = [row[1] for row in await spend_ledger(db, account)]
    assert "rerank_candidates" not in bought


async def test_a_restock_needs_a_visit_since_the_last_one(owner, run_jobs, provider, db):
    """Restocks are lazy and visit-gated: a version bump alone never buys a second one.

    The owner's taste moves twice. The first restock is earned by their visit; the second
    bump finds nobody has been back since, so it costs a queue row and a query rather
    than a shortlist.
    """
    provider.will_say(**ranked(*CANDIDATES))
    account = await rating_films(owner, run_jobs)
    await discovery(owner)
    await run_jobs()
    assert await _reranks(db, account) == 1

    # Their taste moves, twice over, and they never go back to the feed. The bump
    # schedules a restock exactly as it always does, and the restock declines.
    await rate(owner, RATED[0], 4.5)
    await rate(owner, RATED[1], 4.5)
    await run_jobs()
    assert await _reranks(db, account) == 1

    # One arrival is all it takes to earn the next one.
    await discovery(owner)
    await run_jobs()
    assert await _reranks(db, account) == 2


async def test_the_two_halves_of_the_spend_gate_are_stamped_from_one_clock(
    owner, run_jobs, provider, db
):
    """#67 on the spend gate: it compares two columns, so they need a single source.

    ``visited_at`` is the database's ``now()``, and ``restocked_at`` used to be the
    worker's. Those are two machines - Postgres runs in a VM whose clock walks away from
    the host's - and a worker that leads stamps the future, after which every arrival
    finds ``visited_at <= restocked_at`` and declines until the database catches up. A
    silent freeze rather than an overspend, which is the worse kind.

    The skew itself is not reproducible here, because in a test both clocks are the same
    machine's; that is exactly why the bug is invisible until it is in production. What is
    checkable is the ordering the gate stands on, read straight off the row: the stamp
    lands after the visit that earned it, and behind the arrival that follows it. A stamp
    taken from anywhere but the database is free to leave that window.
    """
    provider.will_say(**ranked(*CANDIDATES))
    account = await rating_films(owner, run_jobs)

    await discovery(owner)
    await run_jobs()

    _, visited_at, _, restocked_at, _ = await feed_state(db, account)
    assert visited_at < restocked_at, "stamped before the visit that earned it"

    await discovery(owner)

    _, came_back_at, _, _, _ = await feed_state(db, account)
    assert restocked_at < came_back_at, "stamped ahead of the arrival that follows it"


@tuned(discovery_rerank_window=2)
async def test_a_cut_short_restock_is_resumed_by_the_next_profile_version_bump(
    owner, run_jobs, provider, db
):
    """What the bump is for, once it schedules a restock rather than earning one.

    The provider drops out between the two windows, so the run judges the first and stamps
    nothing. The owner never goes back to the feed - they rate films at the wall until
    their taste earns a regeneration - and the bump that follows picks the run up with no
    fresh arrival anywhere in it. Without this the ``schedule_restock`` call in
    ``regenerate_prose`` reads as dead code to the next person through.
    """
    provider.will_say(**ranked(*CANDIDATES))
    provider.will_fail(llm.ProviderUnavailable("down"), after=1, of=llm.RERANK_SYSTEM)
    account = await rating_films(owner, run_jobs)

    await discovery(owner)
    await run_jobs()
    assert await verdicts(db, account), "the first window should have landed before the drop-out"
    _, arrived_at, _, restocked_at, _ = await feed_state(db, account)
    assert restocked_at is None, "a cut-short run stamped itself"

    provider.recovers().will_say(**ranked(*CANDIDATES))
    await rate(owner, RATED[0], 4.5)
    await rate(owner, RATED[1], 4.5)
    await run_jobs()

    _, still_at, _, restocked_at, _ = await feed_state(db, account)
    assert still_at == arrived_at, "an arrival crept in, so this is not the bump's doing"
    assert restocked_at is not None, "the bump left the run unfinished"
    versions = {row[1] for row in await verdicts(db, account)}
    assert len(versions) == 2, "the resumed run judged nothing at the version that bumped"


# --- Rotation ---


@tuned(discovery_rotation_refreshes=3, discovery_reentry_refreshes=2)
async def test_a_suggestion_passed_over_three_refreshes_rotates_out(owner, run_jobs, provider, db):
    """Passed over, not rejected: the owner has looked at it three times and said nothing.

    Rotation is never announced (surfacing.md) - the shelf is simply its new self - so the
    claim is only ever readable as what is and is not on it.
    """
    account = uuid.UUID(await account_id(owner))
    standing = ids(await stocked_shelf(owner, run_jobs, provider, *CANDIDATES))
    oldest = standing[0]

    for _ in range(2):
        assert oldest in ids(await shelf(owner))

    assert oldest not in ids(await shelf(owner))
    await assert_shelf_stands_on_verdicts(db, account)


@tuned(discovery_rotation_refreshes=3, discovery_reentry_refreshes=2)
async def test_rotation_leaves_the_verdict_cache_untouched(owner, run_jobs, provider, db):
    """The film's judgment is not the reason it left, so nobody pays to think again."""
    account = uuid.UUID(await account_id(owner))
    await stocked_shelf(owner, run_jobs, provider, *CANDIDATES)
    cached = await verdicts(db, account)

    for _ in range(3):
        await shelf(owner)

    assert await verdicts(db, account) == cached


@tuned(discovery_rotation_refreshes=3, discovery_reentry_refreshes=2)
async def test_a_rotated_suggestion_waits_out_a_cooldown_counted_in_refreshes(
    owner, run_jobs, provider, db
):
    """Denominated in the refresh counter and never in calendar time (data-model.md).

    Only three candidates exist, so nothing else can take the rotated film's place: what
    the shelf shows is entirely a statement about the cooldown, and the film returns for
    free when it expires because its verdict never went anywhere.
    """
    account = uuid.UUID(await account_id(owner))
    standing = ids(await stocked_shelf(owner, run_jobs, provider, *CANDIDATES[:3]))

    # The whole shelf arrived at one refresh, so the whole shelf reaches its third
    # together - which is the honest shape of a small pipeline and a stronger claim than
    # singling one card out would be.
    await shelf(owner)
    await shelf(owner)
    assert ids(await shelf(owner)) == []
    assert [row[0] for row in await cooldowns(db, account)] == sorted(standing)

    # Still held, and then not. The only thing that moved is the counter, and it moved
    # because the owner came back rather than because two days went by.
    assert ids(await shelf(owner)) == []
    assert sorted(ids(await shelf(owner))) == sorted(standing)
    assert await verdicts(db, account) != []


@tuned(discovery_rotation_refreshes=3)
async def test_a_card_the_owner_acted_on_never_rotates(owner, run_jobs, provider, db):
    """Rotation is for the cards nobody touched; an action already took its card away."""
    account = uuid.UUID(await account_id(owner))
    await stocked_shelf(owner, run_jobs, provider, *CANDIDATES)

    await accept(owner, CANDIDATES[0])
    for _ in range(4):
        await shelf(owner)

    assert [row[0] for row in await cooldowns(db, account)] != []
    assert CANDIDATES[0].tmdb_id not in [row[0] for row in await cooldowns(db, account)]


@tuned(discovery_rotation_refreshes=3)
async def test_a_backfilled_card_starts_its_own_clock(owner, run_jobs, provider, db):
    """A card that arrived mid-session has survived nothing, and is stamped as much.

    Refreshes survived is the counter now less the arrival stamp, so the assertion does
    that subtraction rather than reading a stored count - which is exactly what the
    engine does, and why the two can never drift apart.
    """
    account = uuid.UUID(await account_id(owner))
    await stocked_shelf(owner, run_jobs, provider, *CANDIDATES)

    await accept(owner, CANDIDATES[0])

    counter = (await feed_state(db, account))[0]
    stamps = {film_id: arrived for film_id, _, arrived in await suggestion_clocks(db, account)}
    joined = max(stamps, key=lambda film_id: stamps[film_id])
    assert counter - stamps[joined] == 0


# --- Fresh markers ---


async def test_the_first_visit_marks_nothing_fresh(owner, run_jobs, provider):
    """There is no last visit to be new since, and saying so about all of them is noise."""
    standing = await stocked_shelf(owner, run_jobs, provider, *CANDIDATES)

    assert [card["fresh"] for card in standing] == [False] * len(standing)


async def test_a_card_that_arrived_since_the_last_visit_is_marked(owner, run_jobs, provider):
    """The one marker the feed has, and it says freshness rather than fit (ADR 0005)."""
    standing = await stocked_shelf(owner, run_jobs, provider, *CANDIDATES)
    await accept(owner, CANDIDATES[0])

    after = await shelf(owner)

    joined = [card for card in after if card["tmdb_id"] not in ids(standing)]
    assert [card["fresh"] for card in joined] == [True]
    assert [card["fresh"] for card in after if card["tmdb_id"] in ids(standing)] == [False, False]


async def test_a_marker_survives_the_session_it_is_shown_in(owner, run_jobs, provider):
    """Every reload after an action marks the same cards; the line moves on arrival only."""
    await stocked_shelf(owner, run_jobs, provider, *CANDIDATES)
    await accept(owner, CANDIDATES[0])
    marked = [card["fresh"] for card in await shelf(owner)]

    await accept(owner, CANDIDATES[1])

    assert [card["fresh"] for card in await shelf(owner, boundary=False)][:2] == marked[1:]


async def test_a_marker_clears_at_the_next_visit(owner, run_jobs, provider):
    """The owner has now seen it, so it is not new any more."""
    await stocked_shelf(owner, run_jobs, provider, *CANDIDATES)
    await accept(owner, CANDIDATES[0])
    assert any(card["fresh"] for card in await shelf(owner))

    assert [card["fresh"] for card in await shelf(owner)] == [False, False, False]


async def test_freshness_moves_nothing_and_says_nothing_at_nav_level(owner, run_jobs, provider):
    """Positions untouched, and no dot: the dot is for the two unlocks and nothing else.

    A fresh card is not promoted, demoted, or counted anywhere outside the shelf. This is
    the whole of surfacing.md's placement for it, and it is the difference between an
    ambient marker and a notification.
    """
    await stocked_shelf(owner, run_jobs, provider, *CANDIDATES)
    await owner.delete("/api/unlocks/discovery")
    before = ids(await shelf(owner))

    await accept(owner, CANDIDATES[0])
    after = await shelf(owner, boundary=False)

    assert ids(after)[:2] == before[1:]
    assert (await unlocks(owner))["discovery"] is False


# --- Boundaries ---


async def test_an_engine_change_lands_only_at_a_session_boundary(owner, run_jobs, provider, db):
    """A restock finishing mid-session writes verdicts, and nothing the owner can see.

    This is the rule the whole read path is arranged around (discovery.md). The restock
    is allowed to land while the owner is reading, so the only way it can be true is for
    the shelf to be re-derived at the arrival rather than written by the job.
    """
    account = uuid.UUID(await account_id(owner))
    provider.will_say(**ranked(*CANDIDATES[:3]))
    await rating_films(owner, run_jobs)
    await discovery(owner)
    await run_jobs()
    standing = ids(await shelf(owner))

    provider.will_say(**ranked(*CANDIDATES))
    await rate(owner, RATED[0], 4.5)
    await rate(owner, RATED[1], 4.5)
    await discovery(owner, boundary=False)
    await run_jobs()

    assert ids(await shelf(owner, boundary=False)) == standing
    assert await verdicts(db, account) != []


async def test_a_reload_after_an_action_queues_no_work(owner, run_jobs, provider, db):
    """The screen reloading is not an arrival, so it moves no clock and buys nothing."""
    account = uuid.UUID(await account_id(owner))
    await stocked_shelf(owner, run_jobs, provider, *CANDIDATES)
    before = await feed_state(db, account)
    spent = await spend_ledger(db, account)

    await shelf(owner, boundary=False)
    await run_jobs()

    assert await feed_state(db, account) == before
    assert await spend_ledger(db, account) == spent


# --- Refusals ---


async def test_an_action_on_a_film_that_is_not_on_the_shelf_is_refused(owner, run_jobs, provider):
    """The three actions are things the owner does to a card, not to a film.

    Adding a film Anchor never suggested is the film page's job, and dismissing one would
    write a suppression for something that was never going to be suggested anyway.
    """
    await stocked_shelf(owner, run_jobs, provider, *CANDIDATES[:3])

    await accept(owner, RATED[0], expect=404)
    await dismiss_suggestion(owner, RATED[0], expect=404)
    await seen_it(owner, RATED[0], expect=404)


async def test_lifting_a_dismissal_that_was_never_made_is_refused(owner, run_jobs, provider):
    await stocked_shelf(owner, run_jobs, provider, *CANDIDATES)

    await lift_dismissal(owner, CANDIDATES[0], expect=404)


async def test_one_account_never_acts_on_another_shelf(owner, other_owner, run_jobs, provider):
    """The account realm, checked at the one surface that takes an id from the client."""
    await stocked_shelf(owner, run_jobs, provider, *CANDIDATES)

    await accept(other_owner, CANDIDATES[0], expect=404)
    await dismiss_suggestion(other_owner, CANDIDATES[0], expect=404)
