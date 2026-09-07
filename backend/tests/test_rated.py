"""The Rated screen: the ordering as band rows, its sorts, and its filters.

The wall is the ordering read back exactly as it is stored, so these tests check that it
says what the placements say - and that every other sort drops the band rows rather than
putting a heading over a sequence that is not in band order.
"""

import pytest

from faketmdb import FilmFixture
from flows import (
    LIBRARY,
    account_id,
    bands_of,
    build_ordering,
    listed_of,
    mark_anchor,
    mark_watched,
    ordering_of,
    queue_of,
    rate,
    rated,
)
from invariants import bands_reported

FIRST, SECOND, THIRD, FOURTH = LIBRARY[:4]


@pytest.fixture(autouse=True)
def stocked(tmdb):
    return tmdb.with_films(*LIBRARY)


# --- The wall ---


async def test_the_wall_is_band_rows_best_band_first(owner):
    await rate(owner, FIRST, 2.0)
    await rate(owner, SECOND, 5.0)
    await rate(owner, THIRD, 3.5)

    screen = await rated(owner)

    assert [row["band"] for row in screen["rows"]] == [5.0, 3.5, 2.0]
    assert ordering_of(screen) == {
        5.0: [SECOND.tmdb_id],
        3.5: [THIRD.tmdb_id],
        2.0: [FIRST.tmdb_id],
    }


async def test_every_poster_carries_its_rank(owner):
    await build_ordering(owner, LIBRARY[:4], band=4.0)

    row = next(row for row in (await rated(owner))["rows"] if row["band"] == 4.0)

    assert [film["rank"] for film in row["films"]] == [1, 2, 3, 4]


async def test_a_band_holding_nothing_is_left_out(owner):
    """The wall shows the ordering, not the scale: an empty row would be a heading over
    nothing."""
    await rate(owner, FIRST, 4.0)

    assert [row["band"] for row in (await rated(owner))["rows"]] == [4.0]


async def test_the_band_shown_is_the_band_stored(owner, db):
    """The rating is the owner's own choice, written down: nothing here derives one."""
    await rate(owner, FIRST, 1.5)
    await rate(owner, SECOND, 4.5)

    await bands_reported(db, await account_id(owner), bands_of(await rated(owner)))


# --- Sorts ---


async def test_a_non_position_sort_goes_flat(owner):
    await rate(owner, FIRST, 2.0)
    await rate(owner, SECOND, 5.0)

    flat = await rated(owner, sort="title")

    assert flat["rows"] is None
    assert [film["tmdb_id"] for film in flat["films"]] == [FIRST.tmdb_id, SECOND.tmdb_id]


async def test_the_recently_rated_sort_reads_the_placement_clock(owner):
    await rate(owner, FIRST, 3.0)
    await rate(owner, SECOND, 3.0)

    flat = await rated(owner, sort="rated")

    assert [film["tmdb_id"] for film in flat["films"]] == [SECOND.tmdb_id, FIRST.tmdb_id]


async def test_a_flat_sort_still_says_every_film_s_band(owner):
    await rate(owner, FIRST, 2.0)
    await rate(owner, SECOND, 5.0)

    assert bands_of(await rated(owner, sort="year")) == {
        FIRST.tmdb_id: 2.0,
        SECOND.tmdb_id: 5.0,
    }


# --- Filters ---


async def test_the_anchors_only_filter_keeps_only_marked_films(owner):
    await build_ordering(owner, LIBRARY[:3], band=4.0)
    await mark_anchor(owner, SECOND)

    filtered = await rated(owner, anchors_only=True)

    assert [film["tmdb_id"] for film in listed_of(filtered)] == [SECOND.tmdb_id]


async def test_a_band_filter_narrows_the_wall(owner):
    await rate(owner, FIRST, 1.0)
    await rate(owner, SECOND, 4.0)
    await rate(owner, THIRD, 5.0)

    filtered = await rated(owner, band_min=4.0)

    assert sorted(ordering_of(filtered)) == [4.0, 5.0]


async def test_a_filter_thins_a_row_without_renumbering_it(owner, tmdb):
    """A rank is a film's place in its band, not its place in the current view."""
    drama = FilmFixture(9201, "A Drama", genres=("Drama",))
    comedy = FilmFixture(9202, "A Comedy", genres=("Comedy",))
    tmdb.with_films(drama, comedy)
    await rate(owner, drama, 4.0)
    await rate(owner, comedy, 4.0)
    whole = ordering_of(await rated(owner))[4.0]

    filtered = await rated(owner, genre="Comedy")

    [only] = filtered["rows"][0]["films"]
    assert only["rank"] == whole.index(9202) + 1
    assert filtered["rows"][0]["size"] == 2, "the row still says how big the band is"


async def test_the_filter_menus_are_computed_over_the_whole_rated_set(owner):
    """Narrowing must never empty the menu that did the narrowing."""
    await rate(owner, FIRST, 1.0)
    await rate(owner, SECOND, 5.0)

    filtered = await rated(owner, band_min=5.0)

    assert filtered["bands"] == [5.0, 1.0]


# --- The rest of the screen ---


async def test_the_rate_later_queue_is_a_section_here(owner):
    await mark_watched(owner, FIRST, "later")

    assert queue_of(await rated(owner)) == [FIRST.tmdb_id]


async def test_the_screen_carries_no_strips(owner):
    """Needs-attention and settling went with drift and settling (ADR 0013)."""
    await rate(owner, FIRST, 4.0)

    screen = await rated(owner)

    assert "needs_attention" not in screen
    assert "settling" not in screen
    assert "provisional" not in screen["rows"][0]["films"][0]


async def test_an_empty_account_reads_as_an_empty_wall(owner):
    screen = await rated(owner)

    assert screen["rows"] == []
    assert screen["bands"] == []
    assert screen["anchor_nudge"] is True
