"""The rolling re-sync: the catalog refreshing itself before TMDB's cache ceiling.

ADR 0003 caps how long TMDB data may be held at six months; the job runs at roughly
five. Age is real calendar time here - it is TMDB's clock, not the owner's - so tests
age a stored film by writing its fetch stamp rather than by freezing anything.
"""

import pytest
from sqlalchemy import select

from anchor import jobs
from anchor.models import Film
from faketmdb import ARRIVAL, FIGHT_CLUB, NOSFERATU

CATALOG = (FIGHT_CLUB, ARRIVAL, NOSFERATU)


@pytest.fixture(autouse=True)
def stocked(tmdb):
    return tmdb.with_films(*CATALOG)


async def fetched_at(db, tmdb_id):
    async with db.sessions() as session:
        return await session.scalar(select(Film.fetched_at).where(Film.tmdb_id == tmdb_id))


async def resync(defer, run_jobs):
    await defer(jobs.resync_stale_films, timestamp=0)
    await run_jobs()


async def test_a_still_referenced_film_past_the_window_is_re_synced(
    owner, db, tmdb, settings, defer, run_jobs, age_film
):
    await owner.post(f"/api/films/{FIGHT_CLUB.tmdb_id}/backlog")
    await age_film(FIGHT_CLUB.tmdb_id, days=settings.film_refresh_days + 1)
    stale_since = await fetched_at(db, FIGHT_CLUB.tmdb_id)

    await resync(defer, run_jobs)

    assert len(tmdb.bundled_calls(FIGHT_CLUB.tmdb_id)) == 2
    assert await fetched_at(db, FIGHT_CLUB.tmdb_id) > stale_since


async def test_a_fresh_film_is_left_alone(owner, tmdb, defer, run_jobs):
    await owner.post(f"/api/films/{FIGHT_CLUB.tmdb_id}/backlog")

    await resync(defer, run_jobs)

    assert len(tmdb.bundled_calls(FIGHT_CLUB.tmdb_id)) == 1


async def test_a_film_no_account_tracks_is_left_to_go_stale(
    owner, db, tmdb, settings, defer, run_jobs, age_film
):
    """Nobody is looking at it, and it re-fetches on next use anyway."""
    await owner.get(f"/api/films/{ARRIVAL.tmdb_id}")
    await age_film(ARRIVAL.tmdb_id, days=settings.film_refresh_days + 1)

    await resync(defer, run_jobs)

    assert len(tmdb.bundled_calls(ARRIVAL.tmdb_id)) == 1


async def test_a_film_pulled_from_tmdb_keeps_the_row_the_catalog_already_has(
    owner, db, tmdb, settings, defer, run_jobs, age_film
):
    await owner.post(f"/api/films/{FIGHT_CLUB.tmdb_id}/backlog")
    await age_film(FIGHT_CLUB.tmdb_id, days=settings.film_refresh_days + 1)
    del tmdb.catalog[FIGHT_CLUB.tmdb_id]

    await resync(defer, run_jobs)

    async with db.sessions() as session:
        assert (await session.get(Film, FIGHT_CLUB.tmdb_id)).title == "Fight Club"


async def test_tmdb_being_down_stops_the_run_rather_than_hammering_it(
    owner, db, tmdb, settings, defer, run_jobs, age_film
):
    for film in (FIGHT_CLUB, ARRIVAL, NOSFERATU):
        await owner.post(f"/api/films/{film.tmdb_id}/backlog")
        await age_film(film.tmdb_id, days=settings.film_refresh_days + 1)
    before = len(tmdb.requests)
    tmdb.down = True

    await resync(defer, run_jobs)

    assert len(tmdb.requests) - before == 1
