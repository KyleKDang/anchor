"""The sync list: what Letterboxd holds, what Anchor holds, and the gap between them.

Anchor never writes to Letterboxd, so the one rating set the owner keeps in two places
is kept in step by hand. Every test here is a version of the same question - which films
would the owner have to retype today, and what would they type - asked at the JSON API
over a real database, with the import driven the way its owner drives it.

The setting is an imported account throughout, because that is the only account that has
a baseline at all: the import is the one thing in the product that ever learns what
Letterboxd holds, and after it the owner's own "synced" is the only thing that writes it.
"""

import pytest

import export
import flows
from export import Row
from faketmdb import FilmFixture
from flows import LIBRARY, account_id, bands_of, rated, re_rate, synced_pairs
from invariants import (
    assert_appended_only,
    assert_ordering_well_formed,
    comparison_log,
    last_synced_ratings,
)

SEEDS = tuple(
    FilmFixture(7900 + n, f"Sync {n}", release_date=f"{1994 + n}-03-01", popularity=25.0 - n)
    for n in range(6)
)
"""Six imported films, one per band from 5.0 down."""

RATINGS = (5.0, 4.5, 4.0, 3.5, 3.0, 2.5)

FRESH = FilmFixture(7990, "Never Logged", release_date="2022-05-01", popularity=9.0)
"""Rated in Anchor and nowhere else: the not-yet-on-Letterboxd section's one film."""


@pytest.fixture(autouse=True)
def stocked(tmdb):
    return tmdb.with_films(*SEEDS, FRESH, *LIBRARY)


@pytest.fixture
async def imported(owner, stocked, run_jobs):
    """An imported library: every film rated, and Letterboxd holding exactly those values."""
    await flows.upload_export(
        owner,
        export.export(
            ratings=tuple(
                Row(film.title, film.year, rating=rating)
                for film, rating in zip(SEEDS, RATINGS, strict=True)
            )
        ),
    )
    await run_jobs()
    return owner


# --- The baseline ---


async def test_a_freshly_imported_account_has_nothing_to_carry_over(imported, db):
    """The two sides agree by construction: the import wrote what Letterboxd holds.

    Nothing is held back to make this true any more. Every imported film is rated and
    final the moment it is matched, and the list is empty because there is genuinely no
    difference yet - which is exactly what the list is for (ADR 0013).
    """
    account = await account_id(imported)

    payload = await flows.sync_list(imported)

    assert payload["changed"] == []
    assert payload["never_recorded"] == []
    assert payload["count"] == 0
    assert await last_synced_ratings(db, account) == {
        film.tmdb_id: rating for film, rating in zip(SEEDS, RATINGS, strict=True)
    }


async def test_a_film_the_owner_re_rated_shows_what_letterboxd_still_holds(imported, db):
    """The list is derived: it is the gap between the baseline and the ordering today."""
    account = await account_id(imported)
    moved = SEEDS[4]
    await re_rate(imported, moved, 1.0)

    payload = await flows.sync_list(imported)

    assert synced_pairs(payload) == {moved.tmdb_id: (RATINGS[4], 1.0)}
    assert payload["count"] == 1
    # The baseline is untouched: only the owner marking it synced ever writes that.
    assert (await last_synced_ratings(db, account))[moved.tmdb_id] == RATINGS[4]


async def test_a_rating_that_wobbles_back_drops_off_the_list_on_its_own(imported, db):
    """Nothing has to be cleaned up: the list stops holding a film that stopped differing."""
    account = await account_id(imported)
    wobbler = SEEDS[4]
    await re_rate(imported, wobbler, 1.0)
    assert wobbler.tmdb_id in synced_pairs(await flows.sync_list(imported))

    await re_rate(imported, wobbler, RATINGS[4])

    payload = await flows.sync_list(imported)
    assert payload["changed"] == []
    assert payload["count"] == 0
    await assert_ordering_well_formed(db, account)


async def test_the_list_reads_in_the_same_order_as_the_rated_screen(imported):
    """Best band first, which is the order the owner already reads their ratings in.

    A worksheet is read top to bottom, so the one thing it must not do is invent a second
    ordering to learn. Pinned because nothing else would notice it changing.
    """
    await re_rate(imported, SEEDS[4], 5.0)
    await re_rate(imported, SEEDS[1], 0.5)

    listed = [row["tmdb_id"] for row in (await flows.sync_list(imported))["changed"]]

    assert listed == [SEEDS[4].tmdb_id, SEEDS[1].tmdb_id], "best band first"


# --- The two sections ---


async def test_a_rating_letterboxd_never_saw_waits_in_its_own_section(imported):
    """A film rated only in Anchor has no old value, so it cannot be an old → new row."""
    await flows.rate(imported, FRESH, 4.0)

    payload = await flows.sync_list(imported)

    assert payload["changed"] == []
    assert synced_pairs(payload, "never_recorded") == {FRESH.tmdb_id: (None, 4.0)}
    assert payload["count"] == 1


# --- Marking synced ---


async def test_marking_a_film_synced_moves_the_baseline_and_nothing_else(imported, db):
    """The owner has retyped it on Letterboxd; Anchor records that and touches nothing."""
    account = await account_id(imported)
    moved = SEEDS[4]
    await re_rate(imported, moved, 1.0)
    before = await comparison_log(db, account)
    wall_before = flows.ordering_of(await rated(imported))

    await flows.mark_synced(imported, moved)

    assert (await flows.sync_list(imported))["count"] == 0
    assert (await last_synced_ratings(db, account))[moved.tmdb_id] == 1.0
    assert flows.ordering_of(await rated(imported)) == wall_before, "it moved the ordering"
    assert_appended_only(before, await comparison_log(db, account), "marking a film synced")


async def test_mark_all_clears_both_sections_in_one_go(imported, db):
    """One control for the owner who has just typed the whole list into Letterboxd."""
    account = await account_id(imported)
    await re_rate(imported, SEEDS[4], 1.0)
    await flows.rate(imported, FRESH, 4.0)
    assert (await flows.sync_list(imported))["count"] == 2

    await flows.mark_all_synced(imported)

    payload = await flows.sync_list(imported)
    assert payload["changed"] == [] and payload["never_recorded"] == []
    assert await last_synced_ratings(db, account) == bands_of(await rated(imported))
    await assert_ordering_well_formed(db, account)


async def test_a_film_with_no_rating_to_carry_over_cannot_be_marked_synced(owner, stocked):
    """Nothing to record, so a baseline would be invented out of nothing."""
    await flows.mark_watched(owner, FRESH, "later")

    await flows.mark_synced(owner, FRESH, expect=409)


async def test_marking_a_film_synced_twice_is_the_same_as_marking_it_once(imported, db):
    """The second tap lands on a film that has already left the list, and is a no-op.

    Worth pinning because the list refreshes underneath the button: a double tap, or a
    mark-all racing a per-film mark, must not turn into an error the owner has to read.
    """
    account = await account_id(imported)
    await re_rate(imported, SEEDS[4], 1.0)
    await flows.mark_synced(imported, SEEDS[4])
    baseline = await last_synced_ratings(db, account)

    await flows.mark_synced(imported, SEEDS[4])

    assert await last_synced_ratings(db, account) == baseline
