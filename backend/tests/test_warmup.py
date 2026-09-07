"""Onboarding, driven the way its owner drives it: pick a way in, warm up, or skip it all.

One skeleton with two fills, so the tests come in two halves that meet at the same
phases. Both fills have three of them and only the middle one differs: the import fill
looks over the wall its export just built, and the fresh fill rates a few more films.
Every test speaks the JSON API over a real database with TMDB faked at its HTTP edge
(testing.md).
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
    backlog,
    browse,
    dismiss_warmup,
    enter_warmup,
    mark_anchor,
    move,
    pool_for,
    profile,
    prompt_for,
    rate,
    rated,
    skip_warmup,
    warmup,
)
from invariants import (
    anchors as anchor_rows,
)
from invariants import assert_ordering_well_formed

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
"""Four films the export rates the same, so one band row holds all four."""

OTHER = FilmFixture(8100, "Other Band", release_date="1995-01-01", vote_count=50)
"""A film in a band of its own, so the wall has more than one row on it."""

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


async def test_the_import_does_not_wipe_the_fork_the_owner_just_answered(owner, run_jobs):
    """Importing is a hard reset of the account's data, and the warmup's marks are not it.

    The owner takes the import branch, which answers the fork, and the export they upload
    a moment later erases everything the account holds. Sending them back to the fork
    they were mid-way through answering is exactly what the reset must not do - the same
    reason it does not log them out.
    """
    await enter_warmup(owner)

    await _import(owner, run_jobs, ratings=_rated_group())

    assert (await warmup(owner))["fork"] is False


async def test_a_skipped_prompt_stays_skipped_across_a_re_import(owner, run_jobs):
    """A skip is the owner saying "stop asking me this", and a new export does not retract it."""
    await _import(owner, run_jobs, ratings=_rated_group())
    await skip_warmup(owner, "anchors", 5.0)

    await _import(owner, run_jobs, ratings=_rated_group())

    assert prompt_for((await warmup(owner))["anchors"], 5.0)["state"] == "skipped"


# --- Phase 1, the fresh fill: search-driven marking ---


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


async def test_an_empty_library_offers_search_and_the_browse_grid_instead(owner):
    """Search is the headline act; the grid is the stated fallback, and says so."""
    phase = (await warmup(owner))["anchors"]

    assert phase["browse"] is True
    assert all(one["candidates"] == [] for one in phase["prompts"])


async def test_a_film_just_rated_is_offered_as_its_band_s_candidate(owner):
    """Rate a film, come back, and mark it: the fresh fill's two taps.

    Candidates are never suggestions in the recommender sense - they are the account's own
    films in that band - so the fresh fill gets them the moment it has any, which is
    exactly what the owner came back to mark.
    """
    await rate(owner, LIBRARY[0], 5.0)

    prompt = prompt_for((await warmup(owner))["anchors"], 5.0)

    assert [film["tmdb_id"] for film in prompt["candidates"]] == [LIBRARY[0].tmdb_id]
    assert prompt["state"] == "todo", "rating it is not marking it"


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


async def test_marking_a_band_is_rate_it_then_mark_it(owner, db):
    """The fresh account's whole bootstrap, in the two acts the owner performs.

    There is no separate designation flow: rate a film, mark it, and the band's pool
    exists (onboarding-and-import.md). The prompt is done the moment one film is in it.
    """
    account = await account_id(owner)
    assert await anchor_rows(db, account) == {}

    await rate(owner, LIBRARY[0], 5.0)
    await mark_anchor(owner, LIBRARY[0])

    prompt = prompt_for((await warmup(owner))["anchors"], 5.0)
    assert prompt["state"] == "done"
    assert [film["tmdb_id"] for film in prompt["marked"]] == [LIBRARY[0].tmdb_id]
    assert await anchor_rows(db, account) == {5.0: [LIBRARY[0].tmdb_id]}
    await assert_ordering_well_formed(db, account)


async def test_a_band_takes_as_many_anchors_as_the_owner_marks(owner):
    """Any number per band, so the prompt never turns a second mark away."""
    await rate(owner, LIBRARY[0], 5.0)
    await mark_anchor(owner, LIBRARY[0])
    await rate(owner, LIBRARY[1], 5.0)
    await mark_anchor(owner, LIBRARY[1])

    prompt = prompt_for((await warmup(owner))["anchors"], 5.0)

    assert len(prompt["marked"]) == 2


# --- The middle and last phases, the fresh fill ---


async def test_the_rating_phase_counts_the_films_rated_after_the_anchors(owner):
    """ "Rate ~5 films you have seen": marking an anchor already rated one."""
    await rate(owner, LIBRARY[0], 5.0)
    await mark_anchor(owner, LIBRARY[0])

    phase = (await warmup(owner))["rating"]
    assert phase["rated"] == 0, "marking is phase one, not phase two"

    await rate(owner, LIBRARY[1], 3.0)

    assert (await warmup(owner))["rating"]["rated"] == 1


async def test_the_rating_phase_stops_asking_at_its_target(owner):
    """Advisory, not a gate: it stops asking, and nothing was ever withheld until it did."""
    for film in LIBRARY[:5]:
        await rate(owner, film, 3.0)

    phase = (await warmup(owner))["rating"]
    assert phase["rated"] == phase["target"] == 5
    assert phase["state"] == "done"


async def test_the_import_fill_swaps_the_rating_phase_for_the_wall(owner, run_jobs):
    """Its middle step is looking over the wall the export just built, not rating more.

    An owner who imported has ratings already; what they have not seen is the ordering
    those ratings made, and edit mode is where they meet it (ADR 0013).
    """
    await _import(owner, run_jobs, ratings=_rated_group())

    state = await warmup(owner)

    assert state["fill"] == "imported"
    assert state["rating"] is None
    assert state["wall"] == {"state": "todo", "moved": 0, "target": 3, "explain": True}


async def test_the_fresh_fill_has_no_wall_step(owner):
    """Nothing was built for it to look over: its middle step is rating a few films."""
    state = await warmup(owner)

    assert state["wall"] is None
    assert state["rating"] is not None


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


async def test_the_import_fill_seeds_the_backlog_before_the_owner_arrives(owner, run_jobs):
    """Phase 3 on the import fill has already happened: watchlist.csv is the whole of it."""
    await _import(owner, run_jobs, ratings=_rated_group(), watchlist=(Row(WANTED.title, 2021),))

    phase = (await warmup(owner))["backlog"]

    assert phase == {"state": "done", "films": 1, "seeded": 1}
    assert [film["tmdb_id"] for film in (await backlog(owner))["films"]] == [WANTED.tmdb_id]


async def test_a_band_keeps_offering_candidates_after_the_first_mark(owner, run_jobs):
    """Any number may be marked per band, so the first mark does not close the offer.

    A band with one anchor is a band that can hold two, and the film that would be the
    second is sitting in the same ranked list the first came from - so the prompt goes on
    offering it rather than sending the owner to the film page to do what it was already
    doing (onboarding-and-import.md).
    """
    await _import(owner, run_jobs, ratings=_rated_group())
    first = prompt_for((await warmup(owner))["anchors"], BAND)["candidates"][0]
    await mark_anchor(owner, _fixture(first["tmdb_id"]))

    prompt = prompt_for((await warmup(owner))["anchors"], BAND)

    assert prompt["state"] == "done", "the question has been answered"
    assert [film["tmdb_id"] for film in prompt["marked"]] == [first["tmdb_id"]]
    assert prompt["candidates"] != [], "and the band is still open to a second one"
    assert first["tmdb_id"] not in [film["tmdb_id"] for film in prompt["candidates"]], (
        "a film already marked is not offered as a candidate for the mark it holds"
    )


# --- The middle phase, the import fill: look over the wall ---


async def test_the_wall_step_completes_once_the_owner_has_moved_a_few_films(owner, run_jobs):
    """Done when the gesture has been used, not when the ordering is right.

    The step exists to introduce dragging, and the wall was already the owner's to edit
    as much or as little as they liked - so what completes it is having moved, and the
    number is advisory like every other one in the warmup.
    """
    await _import(owner, run_jobs, ratings=_rated_group())
    assert (await warmup(owner))["wall"]["state"] == "todo"

    # Each to the end of the row in turn, so every one of the three is a real move: a
    # drop that lands a film where it already sits is not a move and does not count.
    for film in GROUP[:3]:
        await move(owner, film, BAND, len(GROUP))

    phase = (await warmup(owner))["wall"]
    assert phase["moved"] == 3
    assert phase["state"] == "done"


async def test_the_explanation_goes_the_moment_the_owner_has_dragged_anything(owner, run_jobs):
    """One-time, and presence-based like every other ambient line (surfacing.md).

    Nothing records that it was shown: a film the owner has moved is the trace, and it is
    a truer one than a "seen" flag, which would go on hiding the explanation for an owner
    who never worked out what it was explaining.
    """
    await _import(owner, run_jobs, ratings=_rated_group())
    assert (await warmup(owner))["wall"]["explain"] is True

    await move(owner, GROUP[0], BAND, 4)

    phase = (await warmup(owner))["wall"]
    assert phase["explain"] is False, "the gesture has been learned"
    assert phase["state"] == "todo", "which is not the same as the step being done"


async def test_the_explanation_rides_edit_mode_rather_than_the_warmup_screen(owner, run_jobs):
    """It explains dragging, so it belongs where the dragging happens.

    The Rated screen is where the step sends the owner, and the line has to be waiting
    there when they arrive rather than on the page they just left.
    """
    await _import(owner, run_jobs, ratings=_rated_group())
    assert (await rated(owner))["wall_hint"] is True

    await move(owner, GROUP[0], BAND, 4)

    assert (await rated(owner))["wall_hint"] is False


async def test_the_fresh_fill_never_shows_the_wall_s_explanation(owner):
    """It explains a step the fresh fill does not have."""
    await rate(owner, LIBRARY[0], 4.0)

    assert (await rated(owner))["wall_hint"] is False


async def test_the_wall_step_is_skippable_like_every_other(owner, run_jobs):
    await _import(owner, run_jobs, ratings=_rated_group())

    state = await skip_warmup(owner, "wall")

    assert state["wall"]["state"] == "skipped"
    assert state["wall"]["explain"] is False, "put away is put away"
    assert (await rated(owner))["wall_hint"] is False


async def test_a_band_on_the_wall_step_is_refused(owner, run_jobs):
    """Only an anchor prompt names a band; the wall step is one question about one wall."""
    await _import(owner, run_jobs, ratings=_rated_group())

    refused = await skip_warmup(owner, "wall", 3.0, expect=422)

    assert refused["error"]["code"] == "not_a_band_prompt"


@pytest.mark.settings(readiness_forming_films=3, readiness_forming_bands=3)
async def test_a_move_that_crosses_a_bar_names_the_unlock_where_it_happened(owner, run_jobs):
    """The unlock line rides whichever step crossed the bar (surfacing.md).

    On the import fill that step is the wall, and the act is a drop - so the move's own
    answer is what names it, on the screen the owner is looking at. Every later move says
    nothing, because the line is once ever and the nav's dot is its only other half.
    """
    await _import(
        owner,
        run_jobs,
        ratings=(*_rated_group(), Row(OTHER.title, OTHER.year, rating=2.0)),
    )
    assert (await warmup(owner))["readiness"] == "cold", "five films, but only two bands"

    moved = await move(owner, GROUP[0], 1.0, 1)

    assert moved["unlocked"] == ["discovery"]
    # A second crossing of the same bar, which is not a second unlock: the dot fires once
    # per account, and so does the line beside it.
    assert (await move(owner, GROUP[1], 1.0, 1))["unlocked"] == []


async def test_a_move_inside_a_band_unlocks_nothing_and_says_so(owner, run_jobs):
    """Rearranging a band changes neither count readiness reads, so there is nothing to name."""
    await _import(owner, run_jobs, ratings=_rated_group())

    assert (await move(owner, GROUP[0], BAND, 4))["unlocked"] == []


# --- Skipping ---


async def test_every_anchor_prompt_is_individually_skippable(owner):
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
    await skip_warmup(owner, "rating")
    state = await skip_warmup(owner, "rating")

    assert state["rating"]["state"] == "skipped"


async def test_a_band_on_a_phase_that_has_no_bands_is_refused(owner):
    refused = await skip_warmup(owner, "rating", 3.0, expect=422)

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
    await skip_warmup(owner, "rating")
    await skip_warmup(owner, "backlog")
    state = await dismiss_warmup(owner)
    assert state["dismissed"] is True

    await add_to_backlog(owner, LIBRARY[0])
    await rate(owner, LIBRARY[1], 4.0)
    await rate(owner, LIBRARY[2], 2.0)

    assert len((await backlog(owner))["films"]) == 1
    assert len(flows.ordering_of(await rated(owner))) == 2
    assert (await profile(owner))["readiness"] == "cold"
    await assert_ordering_well_formed(db, await account_id(owner))


async def test_skipping_every_step_of_the_import_fill_leaves_the_app_usable_too(owner, run_jobs):
    """The other fill's steps, put away one at a time, and the same promise kept.

    Its middle step is the one the fresh fill does not have, so skipping it is the case
    the fresh test cannot cover: the wall is the owner's to edit whether or not a warmup
    step ever asked them to, and skipping the ask does not take the wall away.
    """
    await _import(owner, run_jobs, ratings=_rated_group(), watchlist=(Row(WANTED.title, 2021),))
    await skip_warmup(owner, "anchors")
    await skip_warmup(owner, "wall")
    await skip_warmup(owner, "backlog")
    assert (await dismiss_warmup(owner))["dismissed"] is True

    await move(owner, GROUP[0], BAND, 4)
    await mark_anchor(owner, GROUP[0])
    await add_to_backlog(owner, LIBRARY[0])

    assert pool_for(await anchors(owner), BAND) == [GROUP[0].tmdb_id]
    assert flows.ordering_of(await rated(owner))[BAND][-1] == GROUP[0].tmdb_id, "the move landed"
    assert len((await backlog(owner))["films"]) == 2


async def test_dismissing_is_not_the_same_as_finishing(owner):
    """Nothing downstream asks whether the warmup finished, so nothing pretends it did."""
    state = await dismiss_warmup(owner)

    assert state["dismissed"] is True
    assert state["anchors"]["state"] == "todo", "put away, not answered"


# --- The invariant ---


async def test_the_app_never_marks_an_anchor_by_itself(owner, run_jobs, db):
    """The one thing the whole warmup may not do, checked over the whole of the warmup.

    Candidates are read, prompts are skipped, the phases run to their targets - and at
    the end of all of it not one anchor exists, because the owner never tapped one.
    Marking is the owner's act in both fills (ADR 0013).
    """
    await _import(owner, run_jobs, ratings=_rated_group(), watchlist=(Row(WANTED.title, 2021),))
    account = await account_id(owner)

    state = await enter_warmup(owner)
    assert any(one["candidates"] for one in state["anchors"]["prompts"]), "candidates were offered"
    await skip_warmup(owner, "anchors")

    assert await anchor_rows(db, account) == {}
    assert pool_for(await anchors(owner), BAND) == [], "and the app is still asking, not acting"


async def test_the_warmup_is_owner_scoped_like_every_other_realm_row(owner, other_owner):
    """One account's skips are invisible to another's, like everything else it owns."""
    await skip_warmup(owner, "anchors", 5.0)

    assert prompt_for((await warmup(other_owner))["anchors"], 5.0)["state"] == "todo"


def _fixture(tmdb_id):
    """The fixture behind a tmdb id, so a test can hand a film back to a flow helper."""
    return next(film for film in (*GROUP, OTHER, WANTED, *LIBRARY) if film.tmdb_id == tmdb_id)
