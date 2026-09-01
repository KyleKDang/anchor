"""TMDB faked at the shared client's HTTP edge, per testing.md.

A small canned catalog answers search and the bundled per-film call, and every
request is recorded, so a test can assert that a film costs exactly one bundled
call. Failures are scripted per test: ``fake.throttle_next(2)`` makes the next two
requests answer 429, and ``fake.down`` makes every request answer 500.
"""

import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs

import httpx

BASE_URL = "https://api.themoviedb.org/3"


@dataclass(frozen=True)
class FilmFixture:
    """One canned film, rendered into the two payload shapes TMDB returns."""

    tmdb_id: int
    title: str
    release_date: str | None = "1999-10-15"
    overview: str = "The narrator meets Tyler Durden, and the ending gives itself away."
    poster_path: str | None = "/poster.jpg"
    backdrop_path: str | None = "/backdrop.jpg"
    runtime: int | None = 139
    genres: tuple[str, ...] = ("Drama", "Thriller")
    keywords: tuple[str, ...] = ("support group",)
    directors: tuple[str, ...] = ("David Fincher",)
    cast: tuple[str, ...] = ("Edward Norton", "Brad Pitt")
    vote_average: float = 8.4
    vote_count: int = 27000

    @property
    def year(self) -> int | None:
        return int(self.release_date[:4]) if self.release_date else None

    def hit(self) -> dict[str, Any]:
        """The film as a search result: no genres, credits, or keywords."""
        return {
            "id": self.tmdb_id,
            "title": self.title,
            "release_date": self.release_date,
            "overview": self.overview,
            "poster_path": self.poster_path,
        }

    def detail(self) -> dict[str, Any]:
        """The film as the bundled detail call returns it, credits and keywords folded in."""
        return {
            **self.hit(),
            "backdrop_path": self.backdrop_path,
            "runtime": self.runtime,
            "genres": [{"id": 100 + i, "name": name} for i, name in enumerate(self.genres)],
            "vote_average": self.vote_average,
            "vote_count": self.vote_count,
            "credits": {
                "cast": [
                    {"id": 200 + i, "name": name, "order": i} for i, name in enumerate(self.cast)
                ],
                "crew": [
                    {"id": 300 + i, "name": name, "job": "Director"}
                    for i, name in enumerate(self.directors)
                ]
                + [{"id": 399, "name": "Someone Else", "job": "Editor"}],
            },
            "keywords": {
                "keywords": [{"id": 400 + i, "name": name} for i, name in enumerate(self.keywords)]
            },
        }


FIGHT_CLUB = FilmFixture(550, "Fight Club")
ARRIVAL = FilmFixture(
    329865,
    "Arrival",
    release_date="2016-11-10",
    genres=("Drama", "Science Fiction"),
    directors=("Denis Villeneuve",),
)
NOSFERATU = FilmFixture(
    653,
    "Nosferatu",
    release_date="1922-03-04",
    genres=("Horror",),
    directors=("F. W. Murnau",),
    poster_path=None,
)


@dataclass
class FakeTmdb:
    """TMDB's HTTP edge: a canned catalog, a request log, and scriptable failures."""

    catalog: dict[int, FilmFixture] = field(default_factory=dict)
    requests: list[httpx.Request] = field(default_factory=list)
    throttled: int = 0
    """Upcoming requests to answer 429 before serving normally."""
    retry_after: str | None = None
    down: bool = False

    def with_films(self, *films: FilmFixture) -> "FakeTmdb":
        self.catalog.update({film.tmdb_id: film for film in films})
        return self

    def throttle_next(self, count: int, retry_after: str | None = None) -> None:
        self.throttled = count
        self.retry_after = retry_after

    def transport(self) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            assert str(request.url).startswith(BASE_URL), request.url
            assert request.headers["authorization"].startswith("Bearer ")
            self.requests.append(request)
            if self.throttled > 0:
                self.throttled -= 1
                headers = {"Retry-After": self.retry_after} if self.retry_after else {}
                return httpx.Response(429, json={"status_message": "slow down"}, headers=headers)
            if self.down:
                return httpx.Response(500, json={"status_message": "tmdb is down"})
            return self._answer(request)

        return httpx.MockTransport(handle)

    def _answer(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/3")
        if path == "/search/movie":
            query = parse_qs(request.url.query.decode())["query"][0].casefold()
            matches = [film for film in self.catalog.values() if query in film.title.casefold()]
            return httpx.Response(200, json={"page": 1, "results": [f.hit() for f in matches]})
        if path.startswith("/movie/"):
            film = self.catalog.get(int(path.removeprefix("/movie/")))
            if film is None:
                return httpx.Response(
                    404, json={"status_message": "The resource you requested could not be found."}
                )
            return httpx.Response(200, json=film.detail())
        raise AssertionError(f"the fake has no answer for {path}")

    # --- What tests assert on ---

    def paths(self) -> list[str]:
        return [request.url.path.removeprefix("/3") for request in self.requests]

    def bundled_calls(self, tmdb_id: int) -> list[httpx.Request]:
        """Every bundled detail call made for one film."""
        return [
            request
            for request in self.requests
            if request.url.path.removesuffix("/").endswith(f"/movie/{tmdb_id}")
        ]

    def payloads(self) -> list[dict[str, Any]]:
        return [json.loads(request.content or b"{}") for request in self.requests]
