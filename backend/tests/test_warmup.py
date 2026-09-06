"""Onboarding, driven the way its owner drives it: pick a way in, warm up, or skip it all.

One skeleton with two fills, so the tests come in two halves that meet at the same
phases. The import fill has two of them for now - its middle step is the wall in edit
mode, which arrives with the warmup ticket that follows this one - and the fresh fill
keeps its three. Every test speaks the JSON API over a real database with TMDB faked at
its HTTP edge (testing.md).
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


async def test_the_import_fill_has_no_rating_phase_yet(owner, run_jobs):
    """Its middle step is the wall in edit mode, which does not exist yet (ADR 0013)."""
    await _import(owner, run_jobs, ratings=_rated_group())

    state = await warmup(owner)

    assert state["fill"] == "imported"
    assert state["rating"] is None


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


async def test_candidates_stop_being_offered_once_the_band_has_an_anchor(owner, run_jobs):
    await _import(owner, run_jobs, ratings=_rated_group())
    prompt = prompt_for((await warmup(owner))["anchors"], BAND)
    await mark_anchor(owner, _fixture(prompt["candidates"][0]["tmdb_id"]))

    prompt = prompt_for((await warmup(owner))["anchors"], BAND)

    assert prompt["state"] == "done"
    assert prompt["candidates"] == [], "the question has been answered, so it stops being asked"
    assert prompt["marked"] != []


async def test_the_import_fill_seeds_the_backlog_before_the_owner_arrives(owner, run_jobs):
    """Phase 3 on the import fill has already happened: watchlist.csv is the whole of it."""
    await _import(owner, run_jobs, ratings=_rated_group(), watchlist=(Row(WANTED.title, 2021),))

    phase = (await warmup(owner))["backlog"]

    assert phase == {"state": "done", "films": 1, "seeded": 1}
    assert [film["tmdb_id"] for film in (await backlog(owner))["films"]] == [WANTED.tmdb_id]


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
