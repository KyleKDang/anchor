"""The Watchlist screen's backlog: what the owner added, sorted and filtered.

Before taste-profile readiness the screen is honestly just this, so the backlog is
the whole surface here. The ranked tier above it arrives with #33.
"""

import pytest

from faketmdb import FilmFixture
from invariants import assert_nothing_rating_shaped

HEAT = FilmFixture(949, "Heat", release_date="1995-12-15", genres=("Crime", "Drama"))
AMELIE = FilmFixture(194, "Amelie", release_date="2001-04-25", genres=("Comedy", "Romance"))
ZODIAC = FilmFixture(1949, "Zodiac", release_date="2007-03-02", genres=("Crime", "Thriller"))
SUNRISE = FilmFixture(631, "Sunrise", release_date=None, genres=("Drama",))

CATALOG = (HEAT, AMELIE, ZODIAC, SUNRISE)


@pytest.fixture(autouse=True)
def stocked(tmdb):
    return tmdb.with_films(*CATALOG)


async def backlog(client, **params):
    response = await client.get("/api/watchlist/backlog", params=params)
    assert response.status_code == 200, response.text
    return response.json()


async def add(client, *films):
    """Add films one at a time, so "recently added" has a real order to sort by."""
    for film in films:
        response = await client.post(f"/api/films/{film.tmdb_id}/backlog")
        assert response.status_code == 200, response.text


def titles(payload):
    return [film["title"] for film in payload["films"]]


async def test_an_empty_backlog_is_empty_not_an_error(owner):
    assert await backlog(owner) == {"films": [], "genres": [], "decades": []}


async def test_the_backlog_shows_what_the_owner_added_newest_first(owner):
    await add(owner, HEAT, AMELIE, ZODIAC)

    payload = await backlog(owner)

    assert titles(payload) == ["Zodiac", "Amelie", "Heat"]
    assert payload["films"][0]["genres"] == ["Crime", "Thriller"]
    assert_nothing_rating_shaped(payload, "the backlog")


async def test_the_backlog_sorts_by_title_and_by_year(owner):
    await add(owner, ZODIAC, HEAT, AMELIE, SUNRISE)

    assert titles(await backlog(owner, sort="title")) == ["Amelie", "Heat", "Sunrise", "Zodiac"]
    # Newest first, and a film with no release year sorts last rather than vanishing.
    assert titles(await backlog(owner, sort="year")) == ["Zodiac", "Amelie", "Heat", "Sunrise"]


async def test_the_backlog_refuses_an_engine_score_sort(owner):
    """ADR 0005: a score-ordered backlog would be a second, undamped ranked tier."""
    response = await owner.get("/api/watchlist/backlog", params={"sort": "score"})

    assert response.status_code == 422


async def test_the_backlog_filters_by_genre(owner):
    await add(owner, HEAT, AMELIE, ZODIAC)

    assert titles(await backlog(owner, genre="Crime")) == ["Zodiac", "Heat"]
    assert titles(await backlog(owner, genre="Romance")) == ["Amelie"]


async def test_the_backlog_filters_by_decade(owner):
    await add(owner, HEAT, AMELIE, ZODIAC, SUNRISE)

    assert titles(await backlog(owner, decade=1990)) == ["Heat"]
    assert titles(await backlog(owner, decade=2000)) == ["Zodiac", "Amelie"]


async def test_a_filter_never_empties_its_own_menu(owner):
    """The offered genres and decades describe the whole backlog, not the filtered view."""
    await add(owner, HEAT, AMELIE, ZODIAC, SUNRISE)

    payload = await backlog(owner, genre="Romance")

    assert titles(payload) == ["Amelie"]
    assert payload["genres"] == ["Comedy", "Crime", "Drama", "Romance", "Thriller"]
    assert payload["decades"] == [2000, 1990]


async def test_a_watched_film_leaves_the_backlog(owner):
    await add(owner, HEAT, AMELIE)

    watched = await owner.post(f"/api/films/{HEAT.tmdb_id}/watched", json={"rate": "later"})

    assert watched.status_code == 200
    assert titles(await backlog(owner)) == ["Amelie"]


async def test_one_owners_backlog_is_never_anothers(owner, other_owner):
    await add(owner, HEAT, AMELIE)
    await add(other_owner, ZODIAC)

    assert titles(await backlog(owner)) == ["Amelie", "Heat"]
    assert titles(await backlog(other_owner)) == ["Zodiac"]


async def test_the_backlog_needs_a_logged_in_account(client):
    response = await client.get("/api/watchlist/backlog")

    assert response.status_code == 401
