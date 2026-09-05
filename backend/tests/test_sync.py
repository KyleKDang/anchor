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
from flows import LIBRARY, account_id, ordering_of, rated, synced_pairs
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
"""Six imported films, one per band from 5.0 down: every divider between them is pinned."""

RATINGS = (5.0, 4.5, 4.0, 3.5, 3.0, 2.5)

TWIN = FilmFixture(7950, "Sync 4 Also", release_date="2011-01-01", popularity=12.0)
"""A second film at 3.0, so that band survives its neighbour leaving it - and comes back.

Without it the wobble has nowhere to wobble to: a band whose only film walks out has its
divider closed up behind it, and the film returning to the same *position* returns to a
different band.
"""

FRESH = FilmFixture(7990, "Never Logged", release_date="2022-05-01", popularity=9.0)
"""Rated in Anchor and nowhere else: the not-yet-on-Letterboxd section's one film."""


@pytest.fixture(autouse=True)
def stocked(tmdb):
    return tmdb.with_films(*SEEDS, TWIN, FRESH, *LIBRARY)


@pytest.fixture
async def imported(owner, stocked, run_jobs):
    """An imported library: every film rated, and Letterboxd holding exactly those values."""
    await flows.upload_export(
        owner,
        export.export(
            ratings=(
                *(
                    Row(film.title, film.year, rating=rating)
                    for film, rating in zip(SEEDS, RATINGS, strict=True)
                ),
                Row(TWIN.title, TWIN.year, rating=3.0),
            )
        ),
    )
    await run_jobs()
    return owner


# --- The baseline ---


async def test_a_freshly_imported_account_has_nothing_to_carry_over(imported, db):
    """The two sides agree by construction, and every position is still a placeholder.

    Both halves of that matter and the empty list is what proves them together: the
    import wrote what Letterboxd holds, and it placed every film provisionally, so
    neither a difference nor a graduated judgment exists yet for the list to show.
    """
    account = await account_id(imported)

    payload = await flows.sync_list(imported)

    assert payload["changed"] == []
    assert payload["never_recorded"] == []
    assert payload["count"] == 0
    assert await last_synced_ratings(db, account) == {
        **{film.tmdb_id: rating for film, rating in zip(SEEDS, RATINGS, strict=True)},
        TWIN.tmdb_id: 3.0,
    }


async def test_a_film_the_owner_has_moved_shows_what_letterboxd_still_holds(imported, db):
    """The list is derived: it is the gap between the baseline and the ordering today.

    Nothing recorded the move as a thing to be synced - the owner settled a film, the
    dividers put it in a different band, and the difference is what the list *is*.
    """
    account = await account_id(imported)
    moved = SEEDS[4]
    order = _ordering(await rated(imported))
    await flows.settle(imported, moved, order, 0)

    payload = await flows.sync_list(imported)

    band = _band_of(await rated(imported), moved.tmdb_id)
    assert synced_pairs(payload) == {moved.tmdb_id: (RATINGS[4], band)}
    assert band != RATINGS[4], "the settle did not move the film out of its band"
    assert payload["count"] == 1
    # The baseline is untouched: only the owner marking it synced ever writes that.
    assert (await last_synced_ratings(db, account))[moved.tmdb_id] == RATINGS[4]


async def test_a_rating_that_wobbles_back_drops_off_the_list_on_its_own(imported, db):
    """Nothing has to be cleaned up: the list stops holding a film that stopped differing."""
    account = await account_id(imported)
    wobbler = SEEDS[4]
    await flows.settle(imported, wobbler, _ordering(await rated(imported)), 0)
    assert wobbler.tmdb_id in synced_pairs(await flows.sync_list(imported))

    # Back beside the twin it left behind, which is where 3.0 still is.
    order = _ordering(await rated(imported))
    await flows.settle(imported, wobbler, order, order.index(SEEDS[5].tmdb_id))

    payload = await flows.sync_list(imported)
    assert payload["changed"] == []
    assert payload["count"] == 0
    assert _band_of(await rated(imported), wobbler.tmdb_id) == RATINGS[4]
    await assert_ordering_well_formed(db, account)


# --- The two sections ---


async def test_a_rating_letterboxd_never_saw_waits_in_its_own_section(imported):
    """A film rated only in Anchor has no old value, so it cannot be an old → new row."""
    await flows.place(imported, FRESH, "a")

    payload = await flows.sync_list(imported)

    assert payload["changed"] == []
    assert synced_pairs(payload, "never_recorded") == {
        FRESH.tmdb_id: (None, _band_of(await rated(imported), FRESH.tmdb_id))
    }
    assert payload["count"] == 1


async def test_a_placement_still_settling_is_held_back_until_it_graduates(owner, stocked):
    """A placeholder position is not a judgment, so there is nothing to carry over yet.

    This is the same rule that leaves the list empty after an import, reached from the
    other direction: an early bail settles the stars and leaves the exact seat open, and
    the owner should not be retyping a value Anchor has not finished deciding. Placed by
    hand rather than imported, because a band has to span several slots for the bail to be
    offered at all and an import puts every film that shares a rating in one slot.
    """
    order = await flows.scale(owner, size=9, top=1, bottom=7)
    await flows.bail_inside_the_band(owner, FRESH, order)

    assert FRESH.tmdb_id not in synced_pairs(await flows.sync_list(owner), "never_recorded")

    await flows.settle(owner, FRESH, _ordering(await rated(owner)), 4)

    assert FRESH.tmdb_id in synced_pairs(await flows.sync_list(owner), "never_recorded")


# --- Marking synced ---


async def test_marking_a_film_synced_moves_the_baseline_and_nothing_else(imported, db):
    """The owner has retyped it on Letterboxd; Anchor records that and touches nothing."""
    account = await account_id(imported)
    moved = SEEDS[4]
    await flows.settle(imported, moved, _ordering(await rated(imported)), 0)
    band = _band_of(await rated(imported), moved.tmdb_id)
    before = await comparison_log(db, account)
    order_before = _ordering(await rated(imported))

    await flows.mark_synced(imported, moved)

    assert (await flows.sync_list(imported))["count"] == 0
    assert (await last_synced_ratings(db, account))[moved.tmdb_id] == band
    assert _ordering(await rated(imported)) == order_before, "marking synced moved the ordering"
    assert_appended_only(before, await comparison_log(db, account), "marking a film synced")


async def test_mark_all_clears_both_sections_in_one_go(imported, db):
    """One control for the owner who has just typed the whole list into Letterboxd."""
    account = await account_id(imported)
    await flows.settle(imported, SEEDS[4], _ordering(await rated(imported)), 0)
    await flows.place(imported, FRESH, "a")
    assert (await flows.sync_list(imported))["count"] == 2

    await flows.mark_all_synced(imported)

    payload = await flows.sync_list(imported)
    assert payload["changed"] == [] and payload["never_recorded"] == []
    baseline = await last_synced_ratings(db, account)
    assert baseline == flows.bands_of(await rated(imported))
    await assert_ordering_well_formed(db, account)


async def test_a_film_with_no_judgment_to_carry_over_cannot_be_marked_synced(owner, stocked):
    """Nothing to record: the position is still a placeholder, so a baseline would be one too."""
    order = await flows.scale(owner, size=9, top=1, bottom=7)
    await flows.bail_inside_the_band(owner, FRESH, order)

    await flows.mark_synced(owner, FRESH, expect=409)


async def test_marking_a_film_synced_twice_is_the_same_as_marking_it_once(imported, db):
    """The second tap lands on a film that has already left the list, and is a no-op.

    Worth pinning because the list refreshes underneath the button: a double tap, or a
    mark-all racing a per-film mark, must not turn into an error the owner has to read.
    """
    account = await account_id(imported)
    await flows.settle(imported, SEEDS[4], _ordering(await rated(imported)), 0)
    await flows.mark_synced(imported, SEEDS[4])
    baseline = await last_synced_ratings(db, account)

    await flows.mark_synced(imported, SEEDS[4])

    assert await last_synced_ratings(db, account) == baseline


# --- Reading the screens ---


def _ordering(payload):
    """Every rated film, best first - tie-group members included, since a settle meets them."""
    return [film_id for slot in ordering_of(payload) for film_id in slot]


def _band_of(payload, tmdb_id):
    return flows.bands_of(payload)[tmdb_id]
