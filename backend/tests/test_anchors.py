"""Anchors: the films the owner is certain of, marked and retired from the film page.

One toggle, any number per band, and nothing else changes when it moves. What the tests
pin is that: a mark is a property of where a film already sits, so marking must never
touch a rating, a rank, or another film.
"""

import pytest

from flows import (
    LIBRARY,
    account_id,
    anchors,
    build_ordering,
    mark_anchor,
    pool_for,
    rate,
    rated,
    re_rate,
    retire_anchor,
)
from invariants import anchors as marked_pools
from invariants import assert_ordering_well_formed, ordering_snapshot

FIRST, SECOND, THIRD, FOURTH = LIBRARY[:4]


@pytest.fixture(autouse=True)
def stocked(tmdb):
    return tmdb.with_films(*LIBRARY)


async def test_a_band_holds_any_number_of_anchors(owner, db):
    """The cap of one per band went with the centroid design (ADR 0013)."""
    await build_ordering(owner, LIBRARY[:3], band=4.5)
    for film in LIBRARY[:3]:
        await mark_anchor(owner, film)

    assert sorted(pool_for(await anchors(owner), 4.5)) == sorted(
        film.tmdb_id for film in LIBRARY[:3]
    )
    await assert_ordering_well_formed(db, await account_id(owner))


async def test_the_pool_reads_most_recently_marked_first(owner):
    """The order the exemplar cap takes its few from (taste-profile.md)."""
    await build_ordering(owner, LIBRARY[:3], band=4.5)
    for film in LIBRARY[:3]:
        await mark_anchor(owner, film)

    assert pool_for(await anchors(owner), 4.5) == [
        THIRD.tmdb_id,
        SECOND.tmdb_id,
        FIRST.tmdb_id,
    ]


async def test_marking_changes_nothing_but_the_mark(owner, db):
    """Not the rating, not the rank, not another film."""
    await build_ordering(owner, LIBRARY[:3], band=3.0)
    account = await account_id(owner)
    before = await ordering_snapshot(db, account)

    await mark_anchor(owner, SECOND)

    assert await ordering_snapshot(db, account) == before


async def test_retiring_is_the_same_toggle_off(owner, db):
    await rate(owner, FIRST, 2.0)
    await mark_anchor(owner, FIRST)
    account = await account_id(owner)
    before = await ordering_snapshot(db, account)

    await retire_anchor(owner, FIRST)

    assert await marked_pools(db, account) == {}
    assert await ordering_snapshot(db, account) == before


async def test_marking_twice_is_marking_once(owner, db):
    """A second tap says nothing new, so it must not reshuffle the pool's recency."""
    await build_ordering(owner, LIBRARY[:2], band=4.0)
    await mark_anchor(owner, FIRST)
    await mark_anchor(owner, SECOND)
    ordered = pool_for(await anchors(owner), 4.0)

    await mark_anchor(owner, FIRST)

    assert pool_for(await anchors(owner), 4.0) == ordered


async def test_only_a_rated_film_can_be_an_anchor(owner):
    """An anchor is a certainty about a band, and an unrated film is in none."""
    await mark_anchor(owner, FIRST, expect=404)


async def test_an_anchor_is_badged_on_the_wall(owner):
    await rate(owner, FIRST, 5.0)
    await mark_anchor(owner, FIRST)

    row = next(row for row in (await rated(owner))["rows"] if row["band"] == 5.0)

    assert row["anchors"] == 1
    assert [film["tmdb_id"] for film in row["films"] if film["anchor"]] == [FIRST.tmdb_id]


async def test_the_band_header_counts_the_whole_pool_not_the_filtered_view(owner):
    """The count is a fact about the band; a filter is a way of looking at it."""
    await build_ordering(owner, LIBRARY[:3], band=4.0)
    for film in LIBRARY[:2]:
        await mark_anchor(owner, film)

    filtered = await rated(owner, anchors_only=True)

    row = next(row for row in filtered["rows"] if row["band"] == 4.0)
    assert row["anchors"] == 2
    assert len(row["films"]) == 2


async def test_a_cross_band_re_rate_retires_and_a_re_mark_is_one_tap(owner, db):
    await rate(owner, FIRST, 5.0)
    await mark_anchor(owner, FIRST)

    await re_rate(owner, FIRST, 3.5)
    assert await marked_pools(db, await account_id(owner)) == {}

    await mark_anchor(owner, FIRST)
    assert await marked_pools(db, await account_id(owner)) == {3.5: [FIRST.tmdb_id]}


async def test_one_owners_anchors_never_show_on_anothers(owner, other_owner):
    await rate(owner, FIRST, 5.0)
    await mark_anchor(owner, FIRST)
    await rate(other_owner, FIRST, 5.0)

    assert pool_for(await anchors(other_owner), 5.0) == []
