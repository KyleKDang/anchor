"""Search, the film page, and the backlog transitions the owner drives from either.

TMDB is faked at the shared client's HTTP edge, so these are the real flows over
canned metadata: search, open a film, add it, take it back out, mark it watched.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from anchor.models import AccountFilm, Film, LifecycleState
from faketmdb import ARRIVAL, FIGHT_CLUB, NOSFERATU
from invariants import assert_nothing_rating_shaped

CATALOG = (FIGHT_CLUB, ARRIVAL, NOSFERATU)


@pytest.fixture(autouse=True)
def stocked(tmdb):
    """Every test in this module searches the same three-film TMDB."""
    return tmdb.with_films(*CATALOG)


async def search(client, query="fight"):
    response = await client.get("/api/films/search", params={"query": query})
    assert response.status_code == 200, response.text
    return response.json()["results"]


async def open_film(client, film=FIGHT_CLUB):
    response = await client.get(f"/api/films/{film.tmdb_id}")
    assert response.status_code == 200, response.text
    return response.json()


async def add_to_backlog(client, film=FIGHT_CLUB):
    response = await client.post(f"/api/films/{film.tmdb_id}/backlog")
    assert response.status_code == 200, response.text
    return response.json()


async def mark_watched(client, film=FIGHT_CLUB):
    response = await client.post(f"/api/films/{film.tmdb_id}/watched")
    assert response.status_code == 200, response.text
    return response.json()


async def state_of(db, tmdb_id):
    async with db.sessions() as session:
        return await session.scalar(select(AccountFilm.state).where(AccountFilm.film_id == tmdb_id))


# --- Search ---


async def test_the_owner_searches_tmdb_and_gets_films_back(owner):
    [result] = await search(owner, "fight")

    assert result["tmdb_id"] == FIGHT_CLUB.tmdb_id
    assert result["title"] == "Fight Club"
    assert result["year"] == 1999
    assert result["poster_path"] == "/poster.jpg"
    assert result["state"] is None
    assert_nothing_rating_shaped(result, "an untracked search result")


async def test_search_does_not_spend_a_bundled_call_on_every_result(owner, tmdb):
    await search(owner, "a")

    assert tmdb.paths() == ["/search/movie"]


async def test_search_flags_the_films_the_owner_already_knows(owner, db):
    await add_to_backlog(owner, FIGHT_CLUB)
    await mark_watched(owner, ARRIVAL)
    # Placement arrives with #27, so the one state nothing here can reach is set directly.
    await add_to_backlog(owner, NOSFERATU)
    async with db.sessions() as session:
        await session.execute(
            update(AccountFilm)
            .where(AccountFilm.film_id == NOSFERATU.tmdb_id)
            .values(state=LifecycleState.rated)
        )
        await session.commit()

    flags = {}
    for film in CATALOG:
        [result] = await search(owner, film.title)
        flags[result["title"]] = (result["state"], result["rating"])

    assert flags == {
        "Fight Club": ("backlog", None),
        "Arrival": ("watched_unrated", None),
        # A rated film's value derives from position against dividers, neither of which
        # exists before #28; the flag itself is what search owes the owner today.
        "Nosferatu": ("rated", None),
    }


async def test_one_owners_flags_never_show_on_anothers_search(owner, other_owner):
    await add_to_backlog(owner, FIGHT_CLUB)

    [result] = await search(other_owner, "fight")

    assert result["state"] is None


async def test_search_needs_a_logged_in_account(client):
    response = await client.get("/api/films/search", params={"query": "fight"})

    assert response.status_code == 401


# --- The film page ---


async def test_opening_a_film_page_shows_its_metadata_and_plot(owner):
    film = await open_film(owner, FIGHT_CLUB)

    assert film["title"] == "Fight Club"
    assert film["year"] == 1999
    assert film["runtime"] == 139
    assert film["genres"] == ["Drama", "Thriller"]
    assert film["directors"] == ["David Fincher"]
    assert film["cast"] == ["Edward Norton", "Brad Pitt"]
    assert film["overview"].startswith("The narrator")
    assert film["state"] is None
    assert_nothing_rating_shaped(film, "an untracked film page")


async def test_a_film_costs_one_bundled_call_however_often_it_is_opened(owner, tmdb):
    for _ in range(3):
        await open_film(owner, FIGHT_CLUB)
    await add_to_backlog(owner, FIGHT_CLUB)

    assert len(tmdb.bundled_calls(FIGHT_CLUB.tmdb_id)) == 1


async def test_the_store_keeps_image_paths_and_a_fetch_stamp_not_bytes(owner, db):
    await open_film(owner, FIGHT_CLUB)

    async with db.sessions() as session:
        film = await session.get(Film, FIGHT_CLUB.tmdb_id)
    assert film.poster_path == "/poster.jpg"
    assert film.backdrop_path == "/backdrop.jpg"
    assert film.fetched_at is not None
    assert not any(isinstance(value, bytes) for value in vars(film).values())


async def test_a_stale_film_is_re_fetched_when_it_is_next_opened(owner, db, tmdb, settings):
    await open_film(owner, FIGHT_CLUB)
    await _age(db, FIGHT_CLUB.tmdb_id, days=settings.film_refresh_days + 1)

    await open_film(owner, FIGHT_CLUB)

    assert len(tmdb.bundled_calls(FIGHT_CLUB.tmdb_id)) == 2


async def test_a_film_tmdb_does_not_have_is_a_404(owner):
    response = await owner.get("/api/films/999999")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "film_not_found"


async def test_tmdb_being_down_says_so_rather_than_failing_obscurely(owner, tmdb):
    tmdb.down = True

    response = await owner.get("/api/films/550")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "tmdb_unavailable"


# --- The backlog transitions ---


async def test_adding_a_film_from_a_search_row_puts_it_in_the_backlog(owner, db):
    film = await add_to_backlog(owner, FIGHT_CLUB)

    assert film["state"] == "backlog"
    assert await state_of(db, FIGHT_CLUB.tmdb_id) is LifecycleState.backlog


async def test_adding_a_film_already_in_the_backlog_changes_nothing(owner, db):
    await add_to_backlog(owner, FIGHT_CLUB)
    await add_to_backlog(owner, FIGHT_CLUB)

    async with db.sessions() as session:
        rows = list(await session.scalars(select(AccountFilm)))
    assert len(rows) == 1


async def test_a_watched_film_cannot_be_pushed_back_into_the_backlog(owner):
    await mark_watched(owner, FIGHT_CLUB)

    response = await owner.post(f"/api/films/{FIGHT_CLUB.tmdb_id}/backlog")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "already_watched"


async def test_removing_a_film_from_the_backlog_leaves_it_untracked(owner, db):
    await add_to_backlog(owner, FIGHT_CLUB)

    response = await owner.delete(f"/api/films/{FIGHT_CLUB.tmdb_id}/backlog")

    assert response.status_code == 204
    assert await state_of(db, FIGHT_CLUB.tmdb_id) is None
    film = await open_film(owner, FIGHT_CLUB)
    assert film["state"] is None


async def test_removing_a_film_that_was_never_added_is_no_error(owner):
    response = await owner.delete(f"/api/films/{FIGHT_CLUB.tmdb_id}/backlog")

    assert response.status_code == 204


async def test_removing_a_watched_film_from_the_backlog_is_refused(owner):
    await mark_watched(owner, FIGHT_CLUB)

    response = await owner.delete(f"/api/films/{FIGHT_CLUB.tmdb_id}/backlog")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "not_in_backlog"


async def test_marking_a_backlog_film_watched_seats_it_in_the_rate_later_queue(owner, db):
    await add_to_backlog(owner, FIGHT_CLUB)

    film = await mark_watched(owner, FIGHT_CLUB)

    assert film["state"] == "watched_unrated"
    async with db.sessions() as session:
        [row] = list(await session.scalars(select(AccountFilm)))
    assert row.state is LifecycleState.watched_unrated
    assert row.rate_later is True


async def test_marking_an_untracked_film_watched_skips_the_backlog_entirely(owner, db):
    await mark_watched(owner, ARRIVAL)

    assert await state_of(db, ARRIVAL.tmdb_id) is LifecycleState.watched_unrated


async def test_the_shared_catalog_survives_the_account_that_filled_it(owner, db):
    """An account wipe clears its realm and never reaches into the shared catalog."""
    await add_to_backlog(owner, FIGHT_CLUB)

    deleted = await owner.request(
        "DELETE", "/api/account", json={"password": "correct horse battery staple"}
    )

    assert deleted.status_code == 204
    async with db.sessions() as session:
        assert await session.get(Film, FIGHT_CLUB.tmdb_id) is not None
        assert list(await session.scalars(select(AccountFilm))) == []


async def _age(db, tmdb_id, *, days):
    """Push a stored film's fetch stamp into the past, as calendar time would."""
    async with db.sessions() as session:
        await session.execute(
            update(Film)
            .where(Film.tmdb_id == tmdb_id)
            .values(fetched_at=datetime.now(UTC) - timedelta(days=days))
        )
        await session.commit()
