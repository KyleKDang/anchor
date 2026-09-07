"""Moves: the owner dragging a film to a new rank or a new band on the wall.

A move is the whole of how a rating gets corrected (rating-system.md, "Moves"): the owner
sees the wall, disagrees with it, and puts the film where it goes. Every drop saves at
once, so every test here is one drop and what the wall says afterwards. Nothing is
asserted about how the wall was drawn - the frontend computes a rank from where the
poster landed, and the endpoint's contract is the rank the film holds once it has.
"""

import pytest
from sqlalchemy import text

from anchor import jobs
from faketmdb import FilmFixture
from flows import (
    LIBRARY,
    account_id,
    bands_of,
    build_ordering,
    mark_anchor,
    mark_watched,
    move,
    ordering_of,
    rate,
    rated,
)
from invariants import anchors, assert_ordering_well_formed, placement_clocks

A, B, C, D, E, F = LIBRARY[:6]


@pytest.fixture(autouse=True)
def stocked(tmdb):
    return tmdb.with_films(*LIBRARY)


async def wall(client, **filters):
    """The wall as plain ids per band, in rank order."""
    return ordering_of(await rated(client, **filters))


async def rank_of(client, film, **filters):
    """The rank stamped on a film's poster, as the current view shows it."""
    for row in (await rated(client, **filters))["rows"]:
        for shown in row["films"]:
            if shown["tmdb_id"] == film.tmdb_id:
                return shown["rank"]
    raise AssertionError(f"{film.tmdb_id} is not on the wall")


async def queued_retrains(jobs_app):
    return [
        job
        for job in await jobs_app.job_manager.list_jobs_async()
        if job.status == "todo" and job.task_name == jobs.task_name(jobs.retrain_taste_profile)
    ]


def by_id(tmdb_id):
    return next(film for film in LIBRARY if film.tmdb_id == tmdb_id)


# --- Within a band ---


async def test_a_move_up_within_a_band_renumbers_only_that_band(owner, db):
    """The films it passes shift down one; the other band is not touched."""
    await build_ordering(owner, [A, B, C, D], band=4.0)
    await build_ordering(owner, [E, F], band=3.0)
    before = await wall(owner)
    a, b, c, d = before[4.0]

    moved = await move(owner, by_id(d), 4.0, 2)

    assert moved == {"tmdb_id": d, "band": 4.0, "rank": 2, "anchor": False}
    after = await wall(owner)
    assert after[4.0] == [a, d, b, c]
    assert after[3.0] == before[3.0]
    await assert_ordering_well_formed(db, await account_id(owner))


async def test_a_move_down_within_a_band_pulls_the_films_it_passes_up(owner, db):
    await build_ordering(owner, [A, B, C, D], band=4.0)
    a, b, c, d = (await wall(owner))[4.0]

    await move(owner, by_id(a), 4.0, 3)

    assert (await wall(owner))[4.0] == [b, c, a, d]
    await assert_ordering_well_formed(db, await account_id(owner))


async def test_a_move_to_either_end_of_its_band(owner, db):
    """Where the keyboard's modifier sends a film: rank 1, and the band's last rank."""
    await build_ordering(owner, [A, B, C, D], band=4.0)
    a, b, c, d = (await wall(owner))[4.0]

    await move(owner, by_id(c), 4.0, 1)
    assert (await wall(owner))[4.0] == [c, a, b, d]

    await move(owner, by_id(c), 4.0, 4)
    assert (await wall(owner))[4.0] == [a, b, d, c]
    await assert_ordering_well_formed(db, await account_id(owner))


async def test_a_move_within_a_band_keeps_the_anchor_and_the_rating(owner, db):
    """Nothing about the band changed, so nothing about its certainty did."""
    await build_ordering(owner, [A, B, C], band=5.0)
    await mark_anchor(owner, A)
    owner_id = await account_id(owner)

    moved = await move(owner, A, 5.0, 3)

    assert moved["anchor"] is True
    assert await anchors(db, owner_id) == {5.0: [A.tmdb_id]}
    assert bands_of(await rated(owner))[A.tmdb_id] == 5.0


async def test_a_move_stamps_moved_at_and_leaves_the_rated_clock_alone(owner, db):
    """A move is not a re-rate: "recently rated" does not restart on a drag."""
    await build_ordering(owner, [A, B, C], band=4.0)
    owner_id = await account_id(owner)
    placed_before, moved_before = (await placement_clocks(db, owner_id))[A.tmdb_id]
    assert moved_before is None, "an unmoved film holds its default rank"

    await move(owner, A, 4.0, 3)

    placed_after, moved_after = (await placement_clocks(db, owner_id))[A.tmdb_id]
    assert moved_after is not None
    assert placed_after == placed_before


async def test_dropping_a_film_where_it_already_is_changes_nothing(owner, db, jobs_app, run_jobs):
    await build_ordering(owner, [A, B, C], band=4.0)
    await run_jobs()
    owner_id = await account_id(owner)
    standing = (await wall(owner))[4.0]
    rank = await rank_of(owner, A)

    moved = await move(owner, A, 4.0, rank)

    assert moved["rank"] == rank
    assert (await wall(owner))[4.0] == standing
    assert (await placement_clocks(db, owner_id))[A.tmdb_id][1] is None, "not a move"
    assert await queued_retrains(jobs_app) == [], "nothing changed, so nothing retrains"


# --- Across bands ---


async def test_a_move_across_bands_renumbers_both_and_changes_the_rating(owner, db):
    await build_ordering(owner, [A, B, C], band=4.0)
    await build_ordering(owner, [D, E], band=3.0)
    before = await wall(owner)
    a, b, c = before[4.0]
    d, e = before[3.0]

    moved = await move(owner, by_id(b), 3.0, 1)

    assert moved["band"] == 3.0 and moved["rank"] == 1
    after = await wall(owner)
    assert after[4.0] == [a, c]
    assert after[3.0] == [b, d, e]
    assert bands_of(await rated(owner))[b] == 3.0
    await assert_ordering_well_formed(db, await account_id(owner))


async def test_a_move_into_the_end_of_another_band(owner, db):
    await build_ordering(owner, [A, B], band=4.0)
    await build_ordering(owner, [C, D], band=3.0)
    c, d = (await wall(owner))[3.0]

    await move(owner, A, 3.0, 3)

    assert (await wall(owner))[3.0] == [c, d, A.tmdb_id]
    await assert_ordering_well_formed(db, await account_id(owner))


async def test_a_move_into_an_empty_band_opens_the_row(owner, db):
    """Every band is a drop target in edit mode, the ones holding nothing included."""
    await build_ordering(owner, [A, B], band=4.0)

    await move(owner, A, 1.5, 1)

    after = await wall(owner)
    assert after[1.5] == [A.tmdb_id]
    assert after[4.0] == [B.tmdb_id]
    await assert_ordering_well_formed(db, await account_id(owner))


async def test_a_move_across_bands_retires_the_anchor(owner, db):
    """A reference that moved is no longer certain (rating-system.md)."""
    await rate(owner, A, 5.0)
    await rate(owner, B, 4.0)
    await mark_anchor(owner, A)
    owner_id = await account_id(owner)
    assert await anchors(db, owner_id) == {5.0: [A.tmdb_id]}

    moved = await move(owner, A, 4.0, 1)

    assert moved["anchor"] is False
    assert await anchors(db, owner_id) == {}
    assert bands_of(await rated(owner))[A.tmdb_id] == 4.0
    await assert_ordering_well_formed(db, owner_id)


async def test_a_move_across_bands_stamps_moved_at(owner, db):
    await rate(owner, A, 5.0)
    owner_id = await account_id(owner)

    await move(owner, A, 4.0, 1)

    assert (await placement_clocks(db, owner_id))[A.tmdb_id][1] is not None


# --- Under a filter ---


async def test_a_drop_between_two_visible_films_lands_directly_after_the_upper_one(owner, db, tmdb):
    """Whatever is hidden between them stays below the film that landed.

    The frontend's rule for a drop is the upper visible film's rank plus one - or that
    rank itself where the film is coming down from above it in the same band - so this
    drives both against a filtered wall and reads the whole band back.
    """
    dramas = [
        FilmFixture(9300 + n, f"Drama {n}", genres=("Drama",), vote_average=8.0 - n / 10)
        for n in range(3)
    ]
    comedies = [
        FilmFixture(9400 + n, f"Comedy {n}", genres=("Comedy",), vote_average=7.0 - n / 10)
        for n in range(2)
    ]
    tmdb.with_films(*dramas, *comedies)
    await build_ordering(owner, dramas + comedies, band=4.0)
    first, second = comedies
    assert (await wall(owner))[4.0] == [9300, 9301, 9302, 9400, 9401], "dramas first by default"
    assert (await wall(owner, genre="Comedy"))[4.0] == [9400, 9401]

    # Dropped at the top of the filtered row: no upper film, so rank 1.
    await move(owner, second, 4.0, 1)
    assert (await wall(owner))[4.0] == [9401, 9300, 9301, 9302, 9400]

    # Dropped back under the other comedy, with three dramas hidden between them. The
    # film is coming down from above it, so once it has left, the upper film is one rank
    # higher than the stamp says, and "directly after it" is the stamp itself.
    upper = await rank_of(owner, first, genre="Comedy")
    await move(owner, second, 4.0, upper)
    assert (await wall(owner))[4.0] == [9300, 9301, 9302, 9400, 9401]
    await assert_ordering_well_formed(db, await account_id(owner))


# --- What a move refuses ---


async def test_a_rank_off_the_end_of_the_band_is_refused(owner, db):
    await build_ordering(owner, [A, B, C], band=4.0)
    await build_ordering(owner, [D], band=3.0)
    before = await wall(owner)

    await move(owner, A, 4.0, 4, expect=422)
    await move(owner, A, 4.0, 0, expect=422)
    await move(owner, A, 3.0, 3, expect=422)

    assert await wall(owner) == before
    await assert_ordering_well_formed(db, await account_id(owner))


async def test_a_band_off_the_scale_is_refused(owner):
    await rate(owner, A, 4.0)

    await move(owner, A, 4.25, 1, expect=422)


async def test_an_unrated_film_cannot_be_moved(owner):
    await mark_watched(owner, A, "later")

    await move(owner, A, 4.0, 1, expect=404)


async def test_a_second_owners_film_cannot_be_moved(owner, other_owner):
    await rate(owner, A, 4.0)

    await move(other_owner, A, 4.0, 1, expect=404)


# --- The retrain ---


async def test_a_burst_of_moves_queues_one_retrain(owner, jobs_app, run_jobs):
    """Every move changes the ordering, and one waiting retrain covers all of them.

    The retrain rebuilds the profile from scratch off the ordering as it stands, so a
    second job queued behind a first that has not started yet would only repeat it.
    """
    await build_ordering(owner, [A, B, C, D, E], band=4.0)
    await run_jobs()
    assert await queued_retrains(jobs_app) == []

    for rank in (5, 1, 3, 2, 4):
        await move(owner, A, 4.0, rank)

    assert len(await queued_retrains(jobs_app)) == 1
    assert await rank_of(owner, A) == 4, "every move still landed"


async def test_a_move_during_a_running_retrain_queues_another(owner, db, jobs_app, run_jobs):
    """Coalescing is with a retrain that is *waiting*.

    One already running may have read the ordering before this move, so the move owes a
    fresh one behind it.
    """
    await build_ordering(owner, [A, B, C], band=4.0)
    await run_jobs()
    await move(owner, A, 4.0, 3)
    [waiting] = await queued_retrains(jobs_app)
    async with db.sessions() as session:
        await session.execute(
            text("UPDATE procrastinate_jobs SET status = 'doing' WHERE id = :id"),
            {"id": waiting.id},
        )
        await session.commit()

    await move(owner, A, 4.0, 1)

    [fresh] = await queued_retrains(jobs_app)
    assert fresh.id != waiting.id
