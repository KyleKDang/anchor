"""A settling sitting: the run over provisional films the Rated strip's button opens.

`test_settling.py` drives the per-film door - one film, asked for by name. This drives
the other one: the owner sitting down to work through whatever is still on the mark, with
the engine choosing what comes next. What is asserted here is the choosing and the
counting, because those are the only things the sitting adds; the placement flow each film
runs through is the same one every other door opens, and is pinned where it lives.

The films are stocked with vote counts that put them in a known order, so a test about the
range can say out loud that the ranking would have chosen differently - the point of the
rule being that the range is asked first and the ranking only breaks its ties.
"""

import pytest

import export
import flows
from export import Row
from faketmdb import FilmFixture
from flows import LIBRARY, account_id, ordering_of, rated
from invariants import (
    assert_appended_only,
    assert_bands_well_formed,
    assert_ordering_well_formed,
    comparison_log,
)

NARROW = FilmFixture(7800, "Narrow Margin", release_date="1990-09-21", vote_count=100)
"""Answered about and bailed out of, so its landing is loose across a slot or two."""

QUIET = FilmFixture(7801, "Quiet Room", release_date="2004-02-27", vote_count=500)
"""Placed on skips alone, so nothing has been said about it and everything is open."""

WIDE = FilmFixture(7802, "Wide Open", release_date="1994-06-10", vote_count=90_000)
"""The same, and far better known: the pair that tie on range and part on remembering.

Deliberately the higher id of the two, because the ranking ends on the id to stay total -
so a tie-break test whose answer is also the lower id would pass without the ranking."""

SEEDS = tuple(
    FilmFixture(7810 + n, f"Seed {n}", release_date=f"{1996 + n}-04-01", vote_count=9000 - n)
    for n in range(6)
)
"""Six imported films, most popular first: every position a placeholder, none narrowed."""

RATINGS = (5.0, 4.5, 4.0, 3.5, 3.0, 2.5)


@pytest.fixture(autouse=True)
def stocked(tmdb):
    return tmdb.with_films(NARROW, WIDE, QUIET, *SEEDS, *LIBRARY)


async def _import(client, run_jobs, favourites=()):
    """An imported library: every film rated by Letterboxd, every position a placeholder."""
    await flows.upload_export(
        client,
        export.export(
            ratings=tuple(
                Row(film.title, film.year, rating=rating)
                for film, rating in zip(SEEDS, RATINGS, strict=True)
            ),
            favorites=tuple(film.title for film in favourites),
        ),
    )
    await run_jobs()


@pytest.fixture
async def imported(owner, stocked, run_jobs):
    await _import(owner, run_jobs)
    return owner


# --- The strip's count ---


async def test_the_strip_counts_what_is_still_settling_and_says_nothing_when_none_is(
    owner, stocked, run_jobs, db
):
    """The one number the design lets on screen, and the absence that hides the strip.

    Anchors are left out of it because the button will not offer one, and a film that
    graduates leaves it, so the count is what the strip would actually hand over rather
    than a tally of provisional placements.
    """
    assert (await rated(owner))["settling"] == 0, "an empty account had something to settle"

    await _import(owner, run_jobs)
    assert (await rated(owner))["settling"] == len(SEEDS)

    await flows.designate(owner, 4.0, SEEDS[2])
    assert (await rated(owner))["settling"] == len(SEEDS) - 1, "an anchor was counted"

    order = _ordering(await rated(owner))
    await flows.settle(owner, SEEDS[5], order, order.index(SEEDS[5].tmdb_id))
    assert (await rated(owner))["settling"] == len(SEEDS) - 2
    await assert_ordering_well_formed(db, await account_id(owner))


# --- Choosing the next film ---


async def test_the_narrowest_search_is_offered_before_the_best_remembered(owner, stocked, db):
    """The rule in one screen: range first, and remembering only where ranges tie.

    One film has been answered about and bailed out of, so its landing is loose across a
    slot or two; the other was placed on skips alone, so nothing has been said about it
    and its landing is loose across the whole ordering. The wide one is by far the
    better-remembered of the two, and it still waits.
    """
    ids = await flows.scale(owner, size=9, top=1, bottom=7)
    await flows.bail_inside_the_band(owner, NARROW, ids)
    await flows.place(owner, WIDE, "skip")

    offer = await flows.next_settling(owner)

    assert offer["film"]["tmdb_id"] == NARROW.tmdb_id, "the wider search was offered first"
    assert offer["remaining"] == 2
    await assert_bands_well_formed(db, await account_id(owner))


async def test_films_the_search_cannot_separate_are_offered_best_remembered_first(
    owner, stocked, db
):
    """The tie-break, with the tie built rather than hoped for.

    Both films were placed on skips alone, so neither has a single judgment about it and
    the two searches are the same width. All that is left to choose between them is which
    the owner is likelier to remember, and popularity is the last term of that.
    """
    await flows.scale(owner, size=9, top=1, bottom=7)
    await flows.place(owner, QUIET, "skip")
    await flows.place(owner, WIDE, "skip")

    offer = await flows.next_settling(owner)

    assert offer["film"]["tmdb_id"] == WIDE.tmdb_id, "the less-known film was offered first"
    assert offer["remaining"] == 2
    await assert_ordering_well_formed(db, await account_id(owner))


async def test_the_owners_favourites_come_first_however_obscure_they_are(owner, stocked, run_jobs):
    """A film the owner named a favourite jumps the ranking outright.

    The seeds are stocked most-popular-first, so the fifth of them is the last film
    popularity would ever offer. Naming it a favourite in the export puts it first, which
    is the term the warmup's ranking leads with and the one settling inherits.
    """
    await _import(owner, run_jobs, favourites=(SEEDS[4],))

    offer = await flows.next_settling(owner)

    assert offer["film"]["tmdb_id"] == SEEDS[4].tmdb_id


async def test_an_anchor_is_never_offered_however_much_it_is_still_settling(imported, db):
    """The invariant: a sitting that runs to its end never hands over an anchor.

    An anchor is re-placed only from its own page, where the warning that landing outside
    its band retires it can be read first (rating-system.md), so a stream that offered one
    would be a door onto that decision with the warning left off.
    """
    await flows.designate(imported, 4.0, SEEDS[2])

    settleable = len(SEEDS) - 1
    offered = []
    while (offer := await flows.next_settling(imported, offered))["film"] is not None:
        assert offer["remaining"] == settleable - len(offered), "the count outran the offers"
        offered.append(offer["film"]["tmdb_id"])

    assert SEEDS[2].tmdb_id not in offered, "the sitting offered an anchor"
    assert len(offered) == settleable, "the sitting did not offer everything else"
    assert offer["remaining"] == 0
    await assert_ordering_well_formed(db, await account_id(imported))


# --- The sitting itself ---


async def test_leaving_mid_film_keeps_its_answers_and_the_next_sitting_resumes_it(imported):
    """Leaving is free, so the sitting is open-ended rather than a thing to finish.

    Nothing about the sitting is stored, so "resuming" is not a session being restored: it
    is the film still being the narrowest search there is, because the answers that
    narrowed it are in the log where every other door would find them too.
    """
    offered = (await flows.next_settling(imported))["film"]["tmdb_id"]
    film = next(one for one in SEEDS if one.tmdb_id == offered)
    step = await flows.begin(imported, film)
    assert step["done"] is False and step["kind"] == "comparison"
    before = step["answered"]
    await flows.answer(imported, film, step["b"]["tmdb_id"], "a")

    # A whole new sitting: it knows nothing, and lands back on the same film anyway.
    offer = await flows.next_settling(imported)
    assert offer["film"]["tmdb_id"] == film.tmdb_id, "the half-answered film was passed over"

    resumed = await flows.begin(imported, film)
    assert resumed["done"] is False, "the film reopened as landed and lost its answers"
    assert resumed["answered"] == before + 1


async def test_not_this_one_moves_on_and_stores_no_judgment_about_the_film(imported, db):
    """Passing is a fact about this sitting and nothing else.

    It records no judgment, no dislike, and no memory of having been passed - the
    next-film rule already puts barely-remembered films last, so a stored "not this one"
    would be a second, weaker copy of a decision the engine already makes.
    """
    account = await account_id(imported)
    passed = (await flows.next_settling(imported))["film"]["tmdb_id"]
    before = await comparison_log(db, account)

    offer = await flows.next_settling(imported, [passed])

    assert offer["film"]["tmdb_id"] != passed, "the passed film was offered straight back"
    assert offer["remaining"] == len(SEEDS) - 1
    assert_appended_only(before, await comparison_log(db, account))
    assert await comparison_log(db, account) == before, "passing wrote a judgment"

    # And it is a fact about this sitting only: the next one offers it again.
    assert (await flows.next_settling(imported))["film"]["tmdb_id"] == passed


async def test_a_sitting_with_nothing_left_offers_nothing(owner, stocked):
    """The sitting's whole ending: it runs out, and says so without a word of praise."""
    await flows.scale(owner, size=5)

    offer = await flows.next_settling(owner)

    assert offer["film"] is None
    assert offer["remaining"] == 0


# --- Helpers ---


def _ordering(payload):
    """The films of the ordering, best first, one id per slot."""
    return [slot[0] for slot in ordering_of(payload)]
