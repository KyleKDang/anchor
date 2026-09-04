"""Onboarding, driven the way its owner drives it: pick a way in, warm up, or skip it all.

One skeleton with two fills, so the tests come in two halves that meet at the same three
phases. Every test speaks the JSON API over a real database with TMDB faked at its HTTP
edge (testing.md), and none of them names the pair the advisory math offered: which
comparison it picked is its business, and pinning it would be testing the math rather
than the behaviour.
"""

import pytest

import export
import flows
from export import Row
from faketmdb import FilmFixture
from flows import (
    LIBRARY,
    account_id,
    add_to_backlog,
    anchors,
    answer_comparison,
    backlog,
    browse,
    designate,
    dismiss_warmup,
    enter_warmup,
    mark_watched,
    next_comparison,
    place,
    profile,
    prompt_for,
    rated,
    skip_warmup,
    warm_up,
    warmup,
)
from invariants import (
    anchors as anchor_rows,
)
from invariants import (
    assert_bands_well_formed,
    assert_ordering_well_formed,
    assert_seeded_slots_only_shrank,
    comparison_log,
    dividers,
    placement_trust,
    seeded_slots,
)

BAND = 4.0
"""The one band every import fixture here puts its films in, so the group is the subject."""

GROUP = tuple(
    FilmFixture(
        8000 + n,
        f"Group {n:02d}",
        release_date=f"{2000 + n}-03-01",
        popularity=20.0 - n,
        vote_count=5000 - 100 * n,
    )
    for n in range(4)
)
"""Four films the export rates the same, so one provisional tie-group holds all four."""

OTHER = FilmFixture(8100, "Other Band", release_date="1995-01-01", vote_count=50)
"""A film in a band of its own, so the ordering has more than one group to choose between."""

WANTED = FilmFixture(8200, "Wanted Someday", release_date="2021-01-01")
"""Watchlist only: what seeds the backlog."""

ACCLAIMED = FilmFixture(8400, "Acclaimed", release_date="1972-01-01", vote_average=9.9)
"""Adored and obscure, so the two browse grids cannot both put it first."""


@pytest.fixture(autouse=True)
def stocked(tmdb):
    return tmdb.with_films(*GROUP, OTHER, WANTED, ACCLAIMED, *LIBRARY)


async def _import(client, run_jobs, *, ratings=None, **files):
    """Upload an export and let the matcher run, which is one act from the owner's side."""
    await flows.upload_export(client, export.export(ratings=ratings or (), **files))
    await run_jobs()
    return await flows.import_state(client)


async def _answer_until_landed(client, film, verdict):
    """Answer every question the same way until the film lands, bands included."""
    step = await flows.begin(client, film)
    while not step["done"]:
        if step["kind"] == "band":
            step = await flows.answer_band(client, film, step["options"][0]["band"])
            continue
        step = await flows.answer(client, film, step["b"]["tmdb_id"], verdict)
    return step


def _rated_group(band=BAND):
    return tuple(Row(film.title, film.year, rating=band) for film in GROUP)


# --- The entry fork ---


async def test_a_new_account_opens_on_the_entry_fork(owner):
    """Nothing has been chosen yet, so the fork is the one thing onboarding shows."""
    state = await warmup(owner)

    assert state["fork"] is True
    assert state["fill"] == "fresh", "no export, so the fresh fill is what is running"
    assert state["dismissed"] is False


async def test_the_fork_is_never_asked_twice(owner):
    """Either branch answers it: the question is "which way in?", and both ways are answers."""
    entered = await enter_warmup(owner)

    assert entered["fork"] is False
    assert (await warmup(owner))["fork"] is False, "and it stays answered across reads"


async def test_importing_after_starting_fresh_switches_the_fill(owner, run_jobs):
    """The import stays reachable later, and taking it later is not a second-class path.

    The fill is read off what the account holds rather than off the fork's answer, so an
    owner who started fresh and imported a month on gets the import fill from that moment.
    """
    await enter_warmup(owner)
    assert (await warmup(owner))["fill"] == "fresh"

    await _import(owner, run_jobs, ratings=_rated_group())

    assert (await warmup(owner))["fill"] == "imported"


# --- Phase 1, the fresh fill: search-driven designation ---


async def test_the_fresh_fill_prompts_the_five_whole_stars_in_ease_of_recall_order(owner):
    """Best first, then worst, then the middle: the two easiest judgments open the flow."""
    phase = (await warmup(owner))["anchors"]

    assert [one["band"] for one in phase["prompts"]] == [5.0, 1.0, 3.0, 4.0, 2.0]
    assert all(one["state"] == "todo" for one in phase["prompts"])


async def test_the_half_stars_are_offered_only_as_a_continuation(owner):
    """ "A definitive 3.5" is a harder judgment than "a definitive 3", so it comes second."""
    phase = (await warmup(owner))["anchors"]

    assert [one["band"] for one in phase["continuation"]] == [4.5, 0.5, 2.5, 3.5, 1.5]
    assert not any(one["band"] % 1 == 0.5 for one in phase["prompts"])


async def test_the_fresh_fill_offers_no_candidates_and_the_browse_grid_instead(owner):
    """Search is the headline act; the grid is the stated fallback, and says so."""
    phase = (await warmup(owner))["anchors"]

    assert phase["browse"] is True
    assert all(one["candidates"] == [] for one in phase["prompts"])


async def test_the_browse_grid_flags_what_the_owner_already_tracks(owner):
    """The fallback is a search result by another name, so it carries the same flags."""
    await add_to_backlog(owner, LIBRARY[0])

    grid = await browse(owner, "popular")

    rows = {row["tmdb_id"]: row for row in grid["results"]}
    assert rows[LIBRARY[0].tmdb_id]["state"] == "backlog"
    assert rows[LIBRARY[1].tmdb_id]["state"] is None


async def test_the_two_grids_are_different_grids(owner):
    """Popular and top-rated rank on different things, or the fallback offers one list twice."""
    popular = [row["tmdb_id"] for row in (await browse(owner, "popular"))["results"]]
    top_rated = [row["tmdb_id"] for row in (await browse(owner, "top_rated"))["results"]]

    assert sorted(popular) == sorted(top_rated), "the same catalog, read two ways"
    assert top_rated[0] == ACCLAIMED.tmdb_id
    assert popular[0] != ACCLAIMED.tmdb_id, "adored is not the same claim as widely seen"


async def test_a_grid_that_is_neither_is_refused(owner):
    await browse(owner, "trending", expect=422)


async def test_designating_a_film_nobody_placed_erects_the_first_dividers(owner, db):
    """The fresh account's whole bootstrap, in one act the owner performs.

    Designating both places the film and pins the dividers around it, which is how an
    ordering with no structure at all gets its first: before this the account shows
    positions and no stars, because a band nobody has located is honestly underivable.
    """
    account = await account_id(owner)
    assert await dividers(db, account) == {}

    await mark_watched(owner, LIBRARY[0], "now")
    started = await designate(owner, 5.0, LIBRARY[0])
    assert started["outcome"] == "placement", "the comparisons place it, the intent does not"
    landed = await flows.begin(owner, LIBRARY[0])

    assert landed["done"] is True
    assert landed["rating"] == 5.0
    assert (await anchors(owner))["anchors"][0]["film"]["tmdb_id"] == LIBRARY[0].tmdb_id
    assert await dividers(db, account) != {}
    await assert_bands_well_formed(db, account)


async def test_a_designation_the_answers_contradict_is_cancelled_not_forced(owner, db):
    """An intent never overrules a judgment, and that rule does not soften for onboarding.

    The owner says the second film is their 5.0 too, then answers that it is worse than
    the one already there. The answers stand and the designation does not.
    """
    await mark_watched(owner, LIBRARY[0], "now")
    await designate(owner, 5.0, LIBRARY[0])
    await flows.begin(owner, LIBRARY[0])

    await mark_watched(owner, LIBRARY[1], "now")
    await designate(owner, 5.0, LIBRARY[1])
    await _answer_until_landed(owner, LIBRARY[1], "b")

    anchored = (await anchors(owner))["anchors"][0]["film"]
    assert anchored["tmdb_id"] == LIBRARY[0].tmdb_id, "the first anchor was not displaced"
    assert LIBRARY[1].tmdb_id in flows.ordering_of(await rated(owner))[1], "but it is placed"
    await assert_bands_well_formed(db, await account_id(owner))


async def test_a_ballpark_guess_on_the_warmups_own_placement_stays_a_search_seed(owner, db):
    """The one thing onboarding must never do: let a hunch become a band judgment.

    A mid-log hunch pinning a divider would quietly reintroduce the drifting absolute
    scale the whole product exists to escape, so the guess moves one opponent to the
    front of the queue and then it is gone.
    """
    await mark_watched(owner, LIBRARY[0], "now")
    await designate(owner, 5.0, LIBRARY[0])
    await flows.begin(owner, LIBRARY[0])
    account = await account_id(owner)
    before = (await comparison_log(db, account), await dividers(db, account))

    await mark_watched(owner, LIBRARY[1], "now")
    step = await flows.begin(owner, LIBRARY[1], ballpark=5.0)

    assert step["answered"] == 0, "a guess is not an answer"
    assert (await comparison_log(db, account), await dividers(db, account)) == before


# --- Phase 2 and 3, the fresh fill ---


async def test_the_evidence_phase_counts_the_films_logged_after_the_anchors(owner):
    """ "Log ~5 films you have seen": the designations already placed themselves."""
    await mark_watched(owner, LIBRARY[0], "now")
    await designate(owner, 5.0, LIBRARY[0])
    await flows.begin(owner, LIBRARY[0])

    phase = (await warmup(owner))["evidence"]
    assert phase["kind"] == "placements"
    assert phase["answered"] == 0, "designating is phase one, not phase two"

    await place(owner, LIBRARY[1], "b")

    assert (await warmup(owner))["evidence"]["answered"] == 1


async def test_the_evidence_phase_stops_asking_at_its_target(owner):
    """Advisory, not a gate: it stops asking, and nothing was ever withheld until it did."""
    for film in LIBRARY[:5]:
        await place(owner, film, "b")

    phase = (await warmup(owner))["evidence"]
    assert phase["answered"] == phase["target"] == 5
    assert phase["state"] == "done"


async def test_adding_a_film_the_owner_means_to_watch_finishes_the_last_phase(owner):
    """The backlog is usable from minute one, so its phase is done the moment it holds one."""
    assert (await warmup(owner))["backlog"] == {"state": "todo", "films": 0, "seeded": 0}

    await add_to_backlog(owner, LIBRARY[0])

    assert (await warmup(owner))["backlog"]["state"] == "done"


# --- Phase 1, the import fill: ranked candidates ---


async def test_candidates_are_ranked_by_rewatch_then_recency_then_popularity(owner, run_jobs):
    """Every term of the ranking answers one question: which of these is remembered best?

    A film gone back to beats a film rated last week, which beats a film half the world
    has seen; the vote count only ever breaks a tie between two the owner said nothing
    else about.
    """
    await _import(
        owner,
        run_jobs,
        ratings=_rated_group(),
        diary=(
            Row(GROUP[3].title, GROUP[3].year, watched_date="2020-01-01"),
            Row(GROUP[3].title, GROUP[3].year, watched_date="2021-01-01", rewatch=True),
            Row(GROUP[2].title, GROUP[2].year, watched_date="2024-06-01"),
        ),
    )

    prompt = prompt_for((await warmup(owner))["anchors"], BAND)

    ranked = [film["tmdb_id"] for film in prompt["candidates"]]
    assert ranked[0] == GROUP[3].tmdb_id, "the one they went back to"
    assert ranked[1:] == [GROUP[0].tmdb_id, GROUP[1].tmdb_id, GROUP[2].tmdb_id], (
        "then the rest by vote count, since the export rated them all on one day"
    )


async def test_a_profile_favourite_is_boosted_to_the_top_of_its_band(owner, run_jobs):
    """The owner already named these as the ones that matter; nothing outranks that."""
    await _import(
        owner,
        run_jobs,
        ratings=_rated_group(),
        favorites=(GROUP[3].title,),
        diary=(Row(GROUP[0].title, GROUP[0].year, watched_date="2021-01-01", rewatch=True),),
    )

    prompt = prompt_for((await warmup(owner))["anchors"], BAND)

    assert prompt["candidates"][0]["tmdb_id"] == GROUP[3].tmdb_id, (
        "a favourite outranks even a rewatch"
    )


async def test_candidates_stop_being_offered_once_the_band_has_its_anchor(owner, run_jobs):
    await _import(owner, run_jobs, ratings=_rated_group())
    prompt = prompt_for((await warmup(owner))["anchors"], BAND)
    await designate(owner, BAND, _fixture(prompt["candidates"][0]["tmdb_id"]))

    prompt = prompt_for((await warmup(owner))["anchors"], BAND)

    assert prompt["state"] == "done"
    assert prompt["candidates"] == [], "the question has been answered, so it stops being asked"
    assert prompt["film"] is not None


async def test_the_import_fill_seeds_the_backlog_before_the_owner_arrives(owner, run_jobs):
    """Phase 3 on the import fill has already happened: watchlist.csv is the whole of it."""
    await _import(owner, run_jobs, ratings=_rated_group(), watchlist=(Row(WANTED.title, 2021),))

    phase = (await warmup(owner))["backlog"]

    assert phase == {"state": "done", "films": 1, "seeded": 1}
    assert [film["tmdb_id"] for film in (await backlog(owner))["films"]] == [WANTED.tmdb_id]


# --- Phase 2, the import fill: the warmup comparisons ---


async def test_the_warmup_asks_within_the_group_the_ordering_knows_least_about(owner, run_jobs):
    """A freshly imported band is films in no order at all, which is where a question pays.

    The import maps every Letterboxd value onto a band and fabricates no order inside it,
    so within-band order is the whole of what is missing - and the biggest group is where
    the most of it is missing.
    """
    await _import(
        owner,
        run_jobs,
        ratings=(*_rated_group(), Row(OTHER.title, OTHER.year, rating=1.0)),
    )

    step = await next_comparison(owner)

    assert step["done"] is False
    assert {step["a"]["tmdb_id"], step["b"]["tmdb_id"]} <= {film.tmdb_id for film in GROUP}


async def test_answering_pulls_both_films_out_of_the_group_in_the_order_given(owner, run_jobs, db):
    """The answer is the two films' first real evidence, and the placeholder shrinks by two.

    Nothing is asserted about the rest of the group: they are still seeded equal, still
    provisional, and a provisional position is a placeholder rather than a judgment.
    """
    await _import(owner, run_jobs, ratings=_rated_group())
    account = await account_id(owner)
    before = await seeded_slots(db, account)
    step = await next_comparison(owner)
    a, b = step["a"]["tmdb_id"], step["b"]["tmdb_id"]

    await answer_comparison(owner, a, b, "a")

    seats = [film for slot in flows.ordering_of(await rated(owner)) for film in slot]
    assert seats.index(a) < seats.index(b), "the winner sits above the loser"
    groups = [slot for slot in await seeded_slots(db, account) if len(slot) > 1]
    assert len(groups) == 1 and sorted(groups[0]) == sorted(
        film.tmdb_id for film in GROUP if film.tmdb_id not in (a, b)
    ), "the four-film group is down to the two nobody was asked about"
    assert_seeded_slots_only_shrank(before, await seeded_slots(db, account), "a warmup comparison")
    await assert_ordering_well_formed(db, account)
    await assert_bands_well_formed(db, account)


async def test_a_tied_answer_makes_a_tie_the_owner_actually_made(owner, run_jobs, db):
    """Seeded equality and judged equality are different things, and only one is a judgment."""
    await _import(owner, run_jobs, ratings=_rated_group())
    account = await account_id(owner)
    step = await next_comparison(owner)
    a, b = step["a"]["tmdb_id"], step["b"]["tmdb_id"]

    await answer_comparison(owner, a, b, "tied")

    slots = flows.ordering_of(await rated(owner))
    assert sorted(next(slot for slot in slots if a in slot)) == sorted([a, b])
    await assert_ordering_well_formed(db, account)


async def test_a_warmup_comparison_never_moves_a_film_across_a_divider(owner, run_jobs, db):
    """Within-band order is what the phase is for; the bands were the import's own claim."""
    await _import(owner, run_jobs, ratings=_rated_group())
    account = await account_id(owner)
    before = flows.bands_of(await rated(owner))

    await warm_up(owner)

    assert flows.bands_of(await rated(owner)) == before
    await assert_bands_well_formed(db, account)


async def test_a_skipped_pair_is_never_offered_again(owner, run_jobs):
    """Declining to compare two films is an answer about the question, not a non-event."""
    await _import(owner, run_jobs, ratings=_rated_group())
    step = await next_comparison(owner)
    pair = frozenset({step["a"]["tmdb_id"], step["b"]["tmdb_id"]})

    after = await answer_comparison(owner, step["a"]["tmdb_id"], step["b"]["tmdb_id"], "skip")

    assert after["answered"] == 0, "a skip records no judgment, so it counts as none"
    assert after["done"] is False, "five pairs in that group are still unasked"
    assert frozenset({after["a"]["tmdb_id"], after["b"]["tmdb_id"]}) != pair


async def test_the_phase_stops_at_its_target(owner, tmdb, run_jobs):
    """Ten is where it stops asking, not a bar the owner has to clear to use anything.

    Twenty-two films in the band, because every answer takes two of them out of the
    group: a real export's bands hold dozens, and this is the smallest one where the
    target is what stops the phase rather than the library running out.
    """
    many = tuple(
        FilmFixture(8300 + n, f"Many {n:02d}", release_date=f"{1990 + n}-01-01") for n in range(22)
    )
    tmdb.with_films(*many)
    await _import(
        owner, run_jobs, ratings=tuple(Row(film.title, film.year, rating=BAND) for film in many)
    )

    answered, done = await warm_up(owner)

    assert done["done"] is True
    assert len(answered) == done["target"] == 10
    assert done["answered"] == 10


async def test_the_phase_stops_early_when_there_is_nothing_left_to_split(owner, run_jobs):
    """A small library runs out of questions before the target, and says so rather than
    inventing one: four films is two answers, and then the ordering knows all of it."""
    await _import(owner, run_jobs, ratings=_rated_group())

    answered, done = await warm_up(owner)

    assert done["done"] is True
    assert len(answered) == 2 < done["target"]


async def test_answering_a_pair_that_is_no_longer_grouped_is_refused(owner, run_jobs):
    """A stale answer would write a judgment against a group that no longer exists.

    The log is append-only, so there would be no taking it back: refusing is the only
    honest answer to a screen left open while the ordering moved on.
    """
    await _import(owner, run_jobs, ratings=_rated_group())
    step = await next_comparison(owner)
    a, b = step["a"]["tmdb_id"], step["b"]["tmdb_id"]
    await answer_comparison(owner, a, b, "a")

    answered = await answer_comparison(owner, a, b, "a", expect=409)

    assert answered["error"]["code"] == "stale_question"


async def test_the_readiness_line_says_only_what_this_answer_crossed(owner, run_jobs):
    """One line on the step the owner is already looking at, and nothing more (ADR 0011).

    Four films is nowhere near any bar, so nothing here crosses one and every step says
    so - the line exists to mark a moment, and inventing moments is what it must not do.
    """
    await _import(owner, run_jobs, ratings=_rated_group())

    _, done = await warm_up(owner)

    assert done["unlocked"] is None
    assert (await warmup(owner))["readiness"] == (await profile(owner))["readiness"]


# --- Skipping ---


async def test_every_designation_prompt_is_individually_skippable(owner):
    """One band put away leaves the other nine exactly where they were."""
    state = await skip_warmup(owner, "anchors", 3.0)

    assert prompt_for(state["anchors"], 3.0)["state"] == "skipped"
    assert prompt_for(state["anchors"], 5.0)["state"] == "todo"
    assert state["anchors"]["state"] == "todo", "one prompt is not the phase"


async def test_a_whole_phase_is_skippable_too(owner):
    state = await skip_warmup(owner, "anchors")

    assert state["anchors"]["state"] == "skipped"
    assert all(one["state"] == "skipped" for one in state["anchors"]["prompts"])


async def test_skipping_twice_is_something_the_owner_may_ask_for(owner):
    await skip_warmup(owner, "evidence")
    state = await skip_warmup(owner, "evidence")

    assert state["evidence"]["state"] == "skipped"


async def test_a_band_on_a_phase_that_has_no_bands_is_refused(owner):
    refused = await skip_warmup(owner, "evidence", 3.0, expect=422)

    assert refused["error"]["code"] == "not_a_band_prompt"


async def test_a_value_that_is_not_a_half_star_band_is_refused(owner):
    refused = await skip_warmup(owner, "anchors", 4.2, expect=422)

    assert refused["error"]["code"] == "not_a_band"


async def test_skipping_everything_leaves_the_app_fully_usable(owner, db):
    """The warmup is never a gate, so an owner who skips all of it loses nothing at all.

    Everything the app does without onboarding it does after dismissing onboarding: the
    film store, the backlog, a placement, the ordering, the profile.
    """
    await enter_warmup(owner)
    await skip_warmup(owner, "anchors")
    await skip_warmup(owner, "evidence")
    await skip_warmup(owner, "backlog")
    state = await dismiss_warmup(owner)
    assert state["dismissed"] is True

    await add_to_backlog(owner, LIBRARY[0])
    await place(owner, LIBRARY[1], "b")
    await place(owner, LIBRARY[2], "b")

    assert len((await backlog(owner))["films"]) == 1
    assert len(flows.ordering_of(await rated(owner))) == 2
    assert (await profile(owner))["readiness"] == "cold"
    await assert_ordering_well_formed(db, await account_id(owner))


async def test_dismissing_is_not_the_same_as_finishing(owner):
    """Nothing downstream asks whether the warmup finished, so nothing pretends it did."""
    state = await dismiss_warmup(owner)

    assert state["dismissed"] is True
    assert state["anchors"]["state"] == "todo", "put away, not answered"


# --- The invariant ---


async def test_the_app_never_designates_an_anchor_by_itself(owner, run_jobs, db):
    """The one thing the whole warmup may not do, checked over the whole of the warmup.

    Candidates are read, comparisons are answered, prompts are skipped, the phases run to
    their targets - and at the end of all of it not one anchor exists, because the owner
    never tapped one. Designation is the owner's act in both fills (ADR 0002).
    """
    await _import(owner, run_jobs, ratings=_rated_group(), watchlist=(Row(WANTED.title, 2021),))
    account = await account_id(owner)

    state = await enter_warmup(owner)
    assert any(one["candidates"] for one in state["anchors"]["prompts"]), "candidates were offered"
    await warm_up(owner)
    await skip_warmup(owner, "anchors")

    assert await anchor_rows(db, account) == {}
    assert (await anchors(owner))["nudge"] is True, "and the app is still asking, not acting"


async def test_the_warmup_is_owner_scoped_like_every_other_realm_row(owner, other_owner):
    """One account's skips are invisible to another's, like everything else it owns."""
    await skip_warmup(owner, "anchors", 5.0)

    assert prompt_for((await warmup(other_owner))["anchors"], 5.0)["state"] == "todo"


async def test_provisional_films_graduate_on_their_own_evidence(owner, run_jobs, db):
    """A warmup comparison is the seed's first real evidence, and graduation rides it.

    Nothing here promotes a placement by fiat: the answers have to re-derive the slot the
    film already sits in, which is the same bar a normal placement clears.
    """
    await _import(owner, run_jobs, ratings=_rated_group())
    account = await account_id(owner)
    assert all(trust == "provisional" for trust, _ in (await placement_trust(db, account)).values())

    await warm_up(owner)

    trusted = [trust for trust, _ in (await placement_trust(db, account)).values()]
    assert "full" in trusted, "answers pulled at least one seed out of its placeholder"


def _fixture(tmdb_id):
    """The fixture behind a tmdb id, so a test can hand a film back to a flow helper."""
    return next(film for film in (*GROUP, OTHER, WANTED, *LIBRARY) if film.tmdb_id == tmdb_id)
