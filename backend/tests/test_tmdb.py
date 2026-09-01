"""The shared TMDB client's own rules: one bundled call, self-throttled, 429-aware.

These sit one step below the API seam on purpose - request spacing has no HTTP
surface of its own - and time is injected rather than spent, so the assertions are
on how long the client *would* have waited.
"""

import pytest

from anchor.tmdb import APPENDED, FilmNotInTmdb, TmdbClient, TmdbUnavailable
from faketmdb import ARRIVAL, BASE_URL, FIGHT_CLUB, FakeTmdb


class FakeTime:
    """A clock that moves only when the client sleeps."""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def build(fake: FakeTmdb, time: FakeTime, *, per_second: float = 4.0, attempts: int = 3):
    return TmdbClient(
        access_token="token",
        base_url=BASE_URL,
        requests_per_second=per_second,
        max_attempts=attempts,
        transport=fake.transport(),
        clock=time.clock,
        sleep=time.sleep,
    )


async def test_a_film_costs_exactly_one_bundled_call():
    fake = FakeTmdb().with_films(FIGHT_CLUB)
    film = await build(fake, FakeTime()).film(FIGHT_CLUB.tmdb_id)

    [request] = fake.requests
    assert request.url.params["append_to_response"] == APPENDED
    assert film.title == "Fight Club"
    assert film.year == 1999
    assert film.genres == ["Drama", "Thriller"]
    assert film.keywords == ["support group"]
    assert [person["name"] for person in film.credits["directors"]] == ["David Fincher"]
    assert [person["name"] for person in film.credits["cast"]] == ["Edward Norton", "Brad Pitt"]


async def test_only_image_paths_come_back_never_bytes():
    fake = FakeTmdb().with_films(FIGHT_CLUB)
    film = await build(fake, FakeTime()).film(FIGHT_CLUB.tmdb_id)

    assert film.poster_path == "/poster.jpg"
    assert film.backdrop_path == "/backdrop.jpg"
    assert all(request.url.host == "api.themoviedb.org" for request in fake.requests)


async def test_the_shared_client_spaces_its_requests():
    fake, time = FakeTmdb().with_films(FIGHT_CLUB), FakeTime()
    client = build(fake, time, per_second=4.0)

    for _ in range(3):
        await client.search("fight")

    # The first request goes straight out; each one after it waits its quarter-second.
    assert time.slept == [0.25, 0.25]
    assert len(fake.requests) == 3


async def test_a_throttled_call_waits_out_the_429_and_retries():
    fake, time = FakeTmdb().with_films(FIGHT_CLUB), FakeTime()
    fake.throttle_next(1, retry_after="2")

    film = await build(fake, time).film(FIGHT_CLUB.tmdb_id)

    assert film.tmdb_id == FIGHT_CLUB.tmdb_id
    assert time.slept == [2.0]
    assert len(fake.bundled_calls(FIGHT_CLUB.tmdb_id)) == 2


async def test_a_call_throttled_past_every_attempt_gives_up():
    fake, time = FakeTmdb().with_films(FIGHT_CLUB), FakeTime()
    fake.throttle_next(99)

    with pytest.raises(TmdbUnavailable):
        await build(fake, time, attempts=3).film(FIGHT_CLUB.tmdb_id)

    assert len(fake.requests) == 3
    assert time.slept == [1.0, 1.0]


async def test_a_film_tmdb_does_not_have_is_not_found():
    fake = FakeTmdb().with_films(FIGHT_CLUB)

    with pytest.raises(FilmNotInTmdb):
        await build(fake, FakeTime()).film(ARRIVAL.tmdb_id)


async def test_tmdb_being_down_is_unavailable_not_a_crash():
    fake = FakeTmdb().with_films(FIGHT_CLUB)
    fake.down = True

    with pytest.raises(TmdbUnavailable):
        await build(fake, FakeTime()).search("fight")
