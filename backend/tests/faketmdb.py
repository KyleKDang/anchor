"""TMDB faked at the shared client's HTTP edge, per testing.md.

A small canned catalog answers search and the bundled per-film call, and every
request is recorded, so a test can assert that a film costs exactly one bundled
call. Failures are scripted per test: ``fake.throttle_next(2)`` makes the next two
requests answer 429, and ``fake.down`` makes every request answer 500.
"""

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs

import httpx

BASE_URL = "https://api.themoviedb.org/3"

GENRE_IDS = {
    "Action": 28,
    "Adventure": 12,
    "Animation": 16,
    "Comedy": 35,
    "Crime": 80,
    "Documentary": 99,
    "Drama": 18,
    "Family": 10751,
    "Fantasy": 14,
    "History": 36,
    "Horror": 27,
    "Music": 10402,
    "Mystery": 9648,
    "Romance": 10749,
    "Science Fiction": 878,
    "TV Movie": 10770,
    "Thriller": 53,
    "War": 10752,
    "Western": 37,
}
"""TMDB's real genre vocabulary and its real ids.

Real rather than invented, because the pipeline turns a genre *name* from the feature
space into an id for a discover slice and back again, and a fake with ids of its own
would let that round trip be wrong in a way no test could see.
"""

_NOT_WORD = re.compile(r"[^0-9a-z]+")


def _searchable(title: str) -> str:
    """How forgiving TMDB's own search is, restated rather than imported.

    The real endpoint searches "original, translated and alternative titles" and does not
    care about an accent or a dash, so a fake that matched bytes would refuse hits TMDB
    would return and the matcher's own folding would never be reached. Written out here
    rather than borrowed from ``anchor.letterboxd`` on purpose: a test that imports the
    rule it is checking proves only that the rule equals itself.
    """
    decomposed = unicodedata.normalize("NFKD", title)
    unaccented = "".join(char for char in decomposed if not unicodedata.combining(char))
    return _NOT_WORD.sub(" ", unaccented.casefold()).strip()


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
    popularity: float = 25.0
    original_language: str = "en"

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
            "popularity": self.popularity,
            "genre_ids": [GENRE_IDS[name] for name in self.genres],
            "original_language": self.original_language,
            "vote_average": self.vote_average,
            "vote_count": self.vote_count,
        }

    def detail(self) -> dict[str, Any]:
        """The film as the bundled detail call returns it, credits and keywords folded in."""
        return {
            **self.hit(),
            "backdrop_path": self.backdrop_path,
            "runtime": self.runtime,
            "genres": [{"id": GENRE_IDS[name], "name": name} for name in self.genres],
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


def _people_ids(film: FilmFixture) -> list[int]:
    """The person ids the detail payload would credit, so a slice can be steered at one."""
    detail = film.detail()["credits"]
    return [person["id"] for person in detail["cast"]] + [
        person["id"] for person in detail["crew"] if person["job"] == "Director"
    ]


@dataclass
class FakeTmdb:
    """TMDB's HTTP edge: a canned catalog, a request log, and scriptable failures."""

    catalog: dict[int, FilmFixture] = field(default_factory=dict)
    requests: list[httpx.Request] = field(default_factory=list)
    throttled: int = 0
    """Upcoming requests to answer 429 before serving normally."""
    retry_after: str | None = None
    down: bool = False
    neighbours: dict[int, tuple[FilmFixture, ...]] = field(default_factory=dict)
    """What ``/similar`` and ``/recommendations`` answer, per seed film."""

    def with_films(self, *films: FilmFixture) -> "FakeTmdb":
        self.catalog.update({film.tmdb_id: film for film in films})
        return self

    def with_neighbours(self, seed: int, *films: FilmFixture) -> "FakeTmdb":
        """What TMDB says is near one film. Both neighbour endpoints answer the same set.

        One set rather than two, because nothing in Anchor treats them differently - the
        pipeline unions them and the prefilter scores what comes out - and a fake that
        told them apart would be inviting a test to assert on which endpoint found a film,
        which is exactly the implementation detail the seam exists to hide.
        """
        self.with_films(*films)
        self.neighbours[seed] = films
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
            query = _searchable(parse_qs(request.url.query.decode())["query"][0])
            matches = [film for film in self.catalog.values() if query in _searchable(film.title)]
            return httpx.Response(200, json={"page": 1, "results": [f.hit() for f in matches]})
        if path in ("/movie/popular", "/movie/top_rated"):
            # The two grids differ by what they rank on, and the fake keeps that apart:
            # a test that could not tell them apart would not be testing the fallback.
            ranked = sorted(
                self.catalog.values(),
                key=(
                    (lambda film: -film.popularity)
                    if path.endswith("popular")
                    else (lambda film: -film.vote_average)
                ),
            )
            return httpx.Response(200, json={"page": 1, "results": [f.hit() for f in ranked]})
        if path == "/genre/movie/list":
            return httpx.Response(
                200,
                json={"genres": [{"id": id, "name": name} for name, id in GENRE_IDS.items()]},
            )
        if path == "/discover/movie":
            return httpx.Response(
                200, json={"page": 1, "results": [f.hit() for f in self._steered(request)]}
            )
        if path.endswith(("/similar", "/recommendations")):
            seed = int(path.removeprefix("/movie/").rsplit("/", 1)[0])
            near = self.neighbours.get(seed, ())
            return httpx.Response(200, json={"page": 1, "results": [f.hit() for f in near]})
        if path.startswith("/movie/"):
            film = self.catalog.get(int(path.removeprefix("/movie/")))
            if film is None:
                return httpx.Response(
                    404, json={"status_message": "The resource you requested could not be found."}
                )
            return httpx.Response(200, json=film.detail())
        raise AssertionError(f"the fake has no answer for {path}")

    def _steered(self, request: httpx.Request) -> list[FilmFixture]:
        """The catalog through one discover slice's filters, popular first, as TMDB ranks.

        The filters are answered rather than ignored, because the whole point of a slice
        is that it is pointed somewhere: a fake that returned everything would let a
        pipeline steer at the wrong feature and still pass.
        """
        query = parse_qs(request.url.query.decode())
        genre = query.get("with_genres")
        person = query.get("with_people")
        floor = int(query.get("vote_count.gte", ["0"])[0])
        found = []
        for film in self.catalog.values():
            if genre and int(genre[0]) not in [GENRE_IDS[name] for name in film.genres]:
                continue
            if person and int(person[0]) not in _people_ids(film):
                continue
            if film.vote_count < floor:
                continue
            found.append(film)
        return sorted(found, key=lambda film: -film.popularity)

    # --- What tests assert on ---

    def sliced(self) -> list[dict[str, list[str]]]:
        """The parameters of every discover slice asked for, in order."""
        return [
            parse_qs(request.url.query.decode())
            for request in self.requests
            if request.url.path.removeprefix("/3") == "/discover/movie"
        ]

    def paths(self) -> list[str]:
        return [request.url.path.removeprefix("/3") for request in self.requests]

    def bundled_calls(self, tmdb_id: int) -> list[httpx.Request]:
        """Every bundled detail call made for one film."""
        return [
            request
            for request in self.requests
            if request.url.path.removesuffix("/").endswith(f"/movie/{tmdb_id}")
        ]
