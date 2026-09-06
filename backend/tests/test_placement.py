"""Rating a film on the band picker: the pick, the landing, and the re-rate.

These read as the owner's flows rather than as endpoint checks: mark a film watched, tap
a band, walk away from the picker, come back and change your mind. What the tests pin is
what the owner sees - the band, the rank, the neighbours - and the invariants of
data-model.md, never the default order's arithmetic.
"""

import pytest
from sqlalchemy import select

from anchor.models import AccountFilm, LifecycleState, Placement
from faketmdb import FilmFixture
from flows import (
    LIBRARY,
    abandon,
    account_id,
    build_ordering,
    mark_anchor,
    mark_watched,
    ordering_of,
    pick,
    picker,
    queue_of,
    rate,
    rated,
    re_rate,
)
from invariants import (
    anchors,
    assert_appended_only,
    assert_nothing_rating_shaped,
    assert_ordering_well_formed,
    comparison_log,
    ordering_snapshot,
    placement_clocks,
)

FIRST, SECOND, THIRD, FOURTH = LIBRARY[:4]


@pytest.fixture(autouse=True)
def stocked(tmdb):
    return tmdb.with_films(*LIBRARY)


# --- The pick ---


async def test_one_pick_rates_a_film(owner, db):
    """A single tap is the whole of rating: no questions, and the film is rated."""
    landed = await rate(owner, FIRST, 4.0)

    assert landed["band"] == 4.0
    assert landed["rank"] == 1
    assert landed["band_size"] == 1
    assert await ordering_snapshot(db, await account_id(owner)) == {4.0: [FIRST.tmdb_id]}
    await assert_ordering_well_formed(db, await account_id(owner))


async def test_the_pick_lands_at_the_default_rank(owner, db, tmdb):
    """Films seat themselves in the default order, whatever order they were rated in."""
    widely = FilmFixture(9001, "Widely Loved", vote_average=8.6, vote_count=9000)
    middling = FilmFixture(9002, "Middling", vote_average=6.2, vote_count=9000)
    tmdb.with_films(widely, middling)

    await rate(owner, middling, 4.0)
    landed = await rate(owner, widely, 4.0)

    assert landed["rank"] == 1, "the better-reviewed film seats itself above"
    assert (ordering_of(await rated(owner)))[4.0] == [9001, 9002]


async def test_a_thin_perfect_average_does_not_top_its_row(owner, db, tmdb):
    """The shrinkage: three perfect votes must not beat a film thousands agree on."""
    obscure = FilmFixture(9101, "Obscure Gem", vote_average=10.0, vote_count=3)
    famous = FilmFixture(9102, "Everyone Has Seen It", vote_average=8.3, vote_count=12000)
    tmdb.with_films(obscure, famous)

    await rate(owner, obscure, 4.0)
    await rate(owner, famous, 4.0)

    assert (ordering_of(await rated(owner)))[4.0] == [9102, 9101]


async def test_the_done_screen_names_the_neighbours(owner):
    """The rank is a statement about the band, so the neighbours it names are the band's."""
    await build_ordering(owner, LIBRARY[:3], band=3.5)
    landed = await rate(owner, FOURTH, 3.5)

    row = (ordering_of(await rated(owner)))[3.5]
    seat = row.index(FOURTH.tmdb_id)
    above = landed["neighbours"]["above"]
    below = landed["neighbours"]["below"]
    assert (above["tmdb_id"] if above else None) == (row[seat - 1] if seat else None)
    assert (below["tmdb_id"] if below else None) == (row[seat + 1] if seat + 1 < len(row) else None)


async def test_a_band_off_the_scale_is_refused(owner):
    await mark_watched(owner, FIRST, "now")
    await pick(owner, FIRST, 3.7, expect=422)


async def test_an_unwatched_film_cannot_be_rated(owner):
    """The picker refuses a film the owner has not said they watched."""
    await picker(owner, FIRST, expect=409)
    await pick(owner, FIRST, 4.0, expect=409)


# --- The picker itself ---


async def test_the_picker_shows_every_band_and_its_pool(owner):
    """Ten rows, each with the owner's own references, so a pick is made against them."""
    await rate(owner, FIRST, 5.0)
    await mark_anchor(owner, FIRST)
    await mark_watched(owner, SECOND, "now")

    shown = await picker(owner, SECOND)

    assert [row["band"] for row in shown["bands"]] == [
        5.0,
        4.5,
        4.0,
        3.5,
        3.0,
        2.5,
        2.0,
        1.5,
        1.0,
        0.5,
    ]
    top = next(row for row in shown["bands"] if row["band"] == 5.0)
    assert [film["tmdb_id"] for film in top["pool"]] == [FIRST.tmdb_id]
    assert top["pool_total"] == 1
    assert shown["current_band"] is None, "an unrated film has no band to mark"


async def test_the_picker_marks_the_current_band_on_a_re_rate(owner):
    await rate(owner, FIRST, 2.5)

    shown = await picker(owner, FIRST)

    assert shown["current_band"] == 2.5
    assert shown["current_rank"] == 1


async def test_abandoning_the_picker_leaves_the_film_on_the_rate_later_queue(owner, db):
    """Walking away is free: the seat was taken when the watch was logged."""
    await abandon(owner, FIRST)

    async with db.sessions() as session:
        account_film = await session.scalar(
            select(AccountFilm).where(AccountFilm.film_id == FIRST.tmdb_id)
        )
        assert account_film.state is LifecycleState.watched_unrated
        assert account_film.rate_later is True
    assert queue_of(await rated(owner)) == [FIRST.tmdb_id]


# --- Re-rating ---


async def test_re_rating_into_the_same_band_keeps_the_rank(owner, db):
    """The owner re-affirmed the rating; where they put it inside it was never the question."""
    await build_ordering(owner, LIBRARY[:3], band=3.0)
    row = (ordering_of(await rated(owner)))[3.0]
    subject = next(film for film in LIBRARY[:3] if film.tmdb_id == row[-1])

    landed = await re_rate(owner, subject, 3.0)

    assert landed["rank"] == len(row)
    assert (ordering_of(await rated(owner)))[3.0] == row


async def test_re_rating_into_another_band_takes_the_default_rank_there(owner, db):
    await build_ordering(owner, LIBRARY[:3], band=3.0)
    await build_ordering(owner, LIBRARY[3:5], band=4.5)

    landed = await re_rate(owner, LIBRARY[0], 4.5)

    assert landed["band"] == 4.5
    wall = ordering_of(await rated(owner))
    assert LIBRARY[0].tmdb_id in wall[4.5]
    assert LIBRARY[0].tmdb_id not in wall.get(3.0, [])
    assert wall[4.5].index(LIBRARY[0].tmdb_id) + 1 == landed["rank"]
    await assert_ordering_well_formed(db, await account_id(owner))


async def test_re_rating_across_bands_retires_the_anchor(owner, db):
    """A reference that moved is no longer certain (rating-system.md)."""
    await rate(owner, FIRST, 5.0)
    await mark_anchor(owner, FIRST)
    assert await anchors(db, await account_id(owner)) == {5.0: [FIRST.tmdb_id]}

    landed = await re_rate(owner, FIRST, 4.0)

    assert landed["anchor"] is False
    assert await anchors(db, await account_id(owner)) == {}


async def test_re_rating_within_a_band_keeps_the_anchor(owner, db):
    """Nothing moved, so nothing is retired: the same band is the same certainty."""
    await rate(owner, FIRST, 5.0)
    await mark_anchor(owner, FIRST)

    landed = await re_rate(owner, FIRST, 5.0)

    assert landed["anchor"] is True
    assert await anchors(db, await account_id(owner)) == {5.0: [FIRST.tmdb_id]}


# --- What a rating leaves behind ---


async def test_every_rating_appends_a_band_pick_and_rewrites_nothing(owner, db):
    account = await account_id(owner)
    await rate(owner, FIRST, 4.0)
    before = await comparison_log(db, account)

    await re_rate(owner, FIRST, 2.0)

    after = await comparison_log(db, account)
    assert_appended_only(before, after, "a re-rate")
    assert [row[1] for row in after] == ["band_pick", "band_pick"]
    assert [row[6] for row in after] == [4.0, 2.0]
    assert [row[7] for row in after] == ["placement", "re_placement"]


async def test_a_fresh_rating_holds_the_default_rank(owner, db):
    """``moved_at`` is empty until the owner actually moves the film."""
    await rate(owner, FIRST, 4.0)

    clocks = await placement_clocks(db, await account_id(owner))
    placed_at, moved_at = clocks[FIRST.tmdb_id]
    assert placed_at is not None
    assert moved_at is None


async def test_a_re_rate_restarts_the_recently_rated_clock(owner, db):
    await rate(owner, FIRST, 4.0)
    first = (await placement_clocks(db, await account_id(owner)))[FIRST.tmdb_id][0]

    await re_rate(owner, FIRST, 1.0)

    again = (await placement_clocks(db, await account_id(owner)))[FIRST.tmdb_id][0]
    assert again > first


async def test_the_done_screen_says_nothing_rating_shaped_about_anything_else(owner):
    """The neighbours are cards: identity and poster, never the band they sit in."""
    await build_ordering(owner, LIBRARY[:3], band=3.5)
    landed = await rate(owner, FOURTH, 3.5)

    for side in ("above", "below"):
        neighbour = landed["neighbours"][side]
        if neighbour is not None:
            assert_nothing_rating_shaped(neighbour, "a done-screen neighbour")


async def test_the_anchor_nudge_shows_only_while_the_account_has_none(owner):
    """One ambient line, presence-based, gone at the first anchor (surfacing.md)."""
    landed = await rate(owner, FIRST, 4.0)
    assert landed["anchor_nudge"] is True

    await mark_anchor(owner, FIRST)
    landed = await rate(owner, SECOND, 4.0)
    assert landed["anchor_nudge"] is False


async def test_a_second_owners_rating_is_invisible_here(owner, other_owner):
    """Every account-realm read is owner-scoped, and rating is no exception."""
    await rate(owner, FIRST, 4.0)
    await rate(other_owner, FIRST, 1.0)

    assert ordering_of(await rated(other_owner)) == {1.0: [FIRST.tmdb_id]}
    assert ordering_of(await rated(owner)) == {4.0: [FIRST.tmdb_id]}


async def test_nothing_but_the_owner_writes_a_band_or_a_rank(owner, db, run_jobs):
    """The engine is read-only on the ordering (ADR 0001): a retrain moves nothing."""
    account = await account_id(owner)
    await build_ordering(owner, LIBRARY[:4], band=3.5)
    before = await ordering_snapshot(db, account)

    await run_jobs()

    assert await ordering_snapshot(db, account) == before


async def test_the_placement_is_gone_when_the_film_leaves_the_ordering(owner, db):
    """A rated film has exactly one placement; the invariant helper is what says so."""
    await rate(owner, FIRST, 4.0)
    account = await account_id(owner)

    async with db.sessions() as session:
        placements = list(await session.scalars(select(Placement)))
        assert len(placements) == 1
    await assert_ordering_well_formed(db, account)
