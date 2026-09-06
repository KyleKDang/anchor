"""Search, the film page, and the backlog transitions the owner drives from either.

TMDB is faked at the shared client's HTTP edge, so these are the real flows over
canned metadata: search, open a film, add it, take it back out, mark it watched.
"""

import pytest
from sqlalchemy import select, update

import flows
from anchor.models import AccountFilm, Film, LifecycleState
from faketmdb import ARRIVAL, FIGHT_CLUB, NOSFERATU, FilmFixture
from flows import add_to_backlog
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


async def mark_watched(client, film=FIGHT_CLUB, rate="later"):
    response = await client.post(f"/api/films/{film.tmdb_id}/watched", json={"rate": rate})
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


async def test_a_stale_film_is_re_fetched_when_it_is_next_opened(owner, tmdb, settings, age_film):
    """And the request that pays for the re-fetch is the one that sees the new metadata."""
    await open_film(owner, FIGHT_CLUB)
    await age_film(FIGHT_CLUB.tmdb_id, days=settings.film_refresh_days + 1)
    tmdb.with_films(FilmFixture(FIGHT_CLUB.tmdb_id, "Fight Club", runtime=151))

    film = await open_film(owner, FIGHT_CLUB)

    assert len(tmdb.bundled_calls(FIGHT_CLUB.tmdb_id)) == 2
    assert film["runtime"] == 151


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


# --- The film page of a rated film ---


async def test_a_rated_film_shows_its_band_its_rank_and_its_neighbours(owner):
    """The rank is a statement about the band, so the neighbours it names are the band's."""
    await flows.build_ordering(owner, CATALOG, band=4.0)
    row = flows.ordering_of(await flows.rated(owner))[4.0]

    middle = await open_film(owner, _fixture(row[1]))

    assert middle["rating"] == 4.0
    assert middle["rank"] == 2
    assert middle["band_size"] == 3
    assert middle["neighbours"]["above"]["tmdb_id"] == row[0]
    assert middle["neighbours"]["below"]["tmdb_id"] == row[2]


async def test_an_end_of_a_row_has_no_neighbour_that_way(owner):
    """The honest answer is nothing, not the next band's edge, which it never ranked against."""
    await flows.rate(owner, FIGHT_CLUB, 4.0)
    await flows.rate(owner, ARRIVAL, 1.0)

    page = await open_film(owner, FIGHT_CLUB)

    assert page["neighbours"] == {"above": None, "below": None}


async def test_the_page_carries_the_anchor_toggle_s_current_state(owner):
    await flows.rate(owner, FIGHT_CLUB, 5.0)
    assert (await open_film(owner, FIGHT_CLUB))["anchor"] is False

    await flows.mark_anchor(owner, FIGHT_CLUB)

    assert (await open_film(owner, FIGHT_CLUB))["anchor"] is True


async def test_the_judgment_history_reads_the_log_newest_first(owner):
    """Shown as the owner made them, against the band and rank above (ADR 0013)."""
    await flows.rate(owner, FIGHT_CLUB, 4.0)
    await flows.re_rate(owner, FIGHT_CLUB, 2.0)

    page = await open_film(owner, FIGHT_CLUB)

    assert [(one["kind"], one["band"]) for one in page["judgments"]] == [
        ("band_pick", 2.0),
        ("band_pick", 4.0),
    ]
    assert all(one["other"] is None for one in page["judgments"]), "a pick names one film"


async def test_a_judgment_the_ordering_moved_past_is_shown_unflagged(owner):
    """No status, no supersession: the reader compares it with the ordering itself."""
    await flows.rate(owner, FIGHT_CLUB, 4.0)
    await flows.re_rate(owner, FIGHT_CLUB, 2.0)

    page = await open_film(owner, FIGHT_CLUB)

    assert page["rating"] == 2.0
    assert any(one["band"] == 4.0 for one in page["judgments"]), "the old pick still reads"
    assert all("status" not in one for one in page["judgments"])


async def test_an_unrated_film_page_carries_none_of_it(owner):
    """Absence rather than emptiness: an unwatched film has no rank to have (ADR 0005)."""
    page = await open_film(owner, FIGHT_CLUB)

    assert page["rank"] is None
    assert page["band_size"] is None
    assert page["neighbours"] is None
    assert page["judgments"] == []
    assert_nothing_rating_shaped(page, "an untracked film page")


def _fixture(tmdb_id):
    return next(film for film in CATALOG if film.tmdb_id == tmdb_id)
