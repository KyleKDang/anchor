"""A stand-in for TMDB's HTTP API, for the composed dev stack.

The shared client calls it exactly as it would call TMDB, so the dev stack has a
working search with no credential and the browser smoke suite is deterministic.
It answers the endpoints Anchor uses - ``GET /3/search/movie``, the two browse grids,
the bundled ``GET /3/movie/{id}?append_to_response=credits,keywords``, and discovery's
``/3/discover/movie``, ``/3/movie/{id}/similar``, ``/3/movie/{id}/recommendations`` and
``/3/genre/movie/list`` - over a fixed handful of films. Standard library only. Poster
paths are deliberately absent: nothing here should hotlink real TMDB images.
"""

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

GENRE_IDS = {
    "Action": 28,
    "Comedy": 35,
    "Crime": 80,
    "Drama": 18,
    "Horror": 27,
    "Music": 10402,
    "Mystery": 9648,
    "Romance": 10749,
    "Science Fiction": 878,
    "Thriller": 53,
    "War": 10752,
    "Western": 37,
}
"""TMDB's own ids for the genres this catalog uses.

Real ids rather than invented ones, because discovery turns a genre *name* from the
weight vector into an id for a discover slice and back again: a fake with a private
numbering would let that round trip be broken and still look fine.
"""


def film(
    tmdb_id: int,
    title: str,
    release_date: str,
    genres: list[str],
    director: str,
    popularity: float = 20.0,
    vote_average: float = 7.5,
    vote_count: int = 1000,
    original_language: str = "en",
) -> dict[str, Any]:
    return {
        "id": tmdb_id,
        "title": title,
        "release_date": release_date,
        "overview": f"The plot of {title}, ending and all.",
        "poster_path": None,
        # What the import matcher breaks a tie on, and what ranks its review candidates.
        "popularity": popularity,
        "backdrop_path": None,
        "runtime": 120,
        "genres": [{"id": GENRE_IDS[name], "name": name} for name in genres],
        # The same genres as ids, which is how every list response names them.
        "genre_ids": [GENRE_IDS[name] for name in genres],
        "original_language": original_language,
        "vote_average": vote_average,
        "vote_count": vote_count,
        "credits": {
            "cast": [{"id": 201, "name": "A Lead", "order": 0}],
            "crew": [{"id": 301 + tmdb_id % 97, "name": director, "job": "Director"}],
        },
        "keywords": {"keywords": [{"id": 401, "name": "dev fixture"}]},
    }


# Films spread across genres and decades on purpose: the Rated screen groups an ordering
# into bands and filters it, and neither shows anything worth looking at until the dev
# stack holds enough films to make more than one band and more than one decade. The
# popularity and vote figures disagree with each other on purpose too, so the warmup's two
# browse grids come out as two different lists rather than one list twice.
#
# The second block is there for discovery: the feed only ever suggests films the owner has
# never tracked, so a catalog they can rate to the last film is a catalog whose shelf is
# always empty. Their vote counts run low, which is also what the prefilter's damper
# prefers - a dev stack whose suggestions are the seven most famous films in it would
# hide exactly the behaviour the damper exists for.
CATALOG = {
    550: film(550, "Fight Club", "1999-10-15", ["Drama", "Thriller"], "David Fincher", 42.0, 8.4),
    329865: film(
        329865, "Arrival", "2016-11-10", ["Drama", "Science Fiction"], "Denis Villeneuve", 31.0, 7.6
    ),
    949: film(949, "Heat", "1995-12-15", ["Crime", "Drama"], "Michael Mann", 18.0, 7.9),
    496243: film(
        496243, "Parasite", "2019-05-30", ["Comedy", "Thriller"], "Bong Joon-ho", 27.0, 8.5
    ),
    244786: film(
        244786, "Whiplash", "2014-10-10", ["Drama", "Music"], "Damien Chazelle", 22.0, 8.4
    ),
    348: film(348, "Alien", "1979-05-25", ["Horror", "Science Fiction"], "Ridley Scott", 25.0, 8.2),
    11104: film(
        11104, "Chungking Express", "1994-07-14", ["Drama", "Romance"], "Wong Kar-wai", 9.0, 7.7
    ),
    242: film(
        242,
        "The Night of the Hunter",
        "1955-09-29",
        ["Crime", "Thriller"],
        "Charles Laughton",
        6.0,
        8.0,
        300,
    ),
    1091: film(
        1091,
        "The Thing",
        "1982-06-25",
        ["Horror", "Mystery"],
        "John Carpenter",
        12.0,
        8.1,
        420,
    ),
    3782: film(
        3782,
        "Come and See",
        "1985-07-09",
        ["Drama", "War"],
        "Elem Klimov",
        5.0,
        8.3,
        260,
        "ru",
    ),
    11216: film(
        11216,
        "Cinema Paradiso",
        "1988-11-17",
        ["Drama", "Romance"],
        "Giuseppe Tornatore",
        7.0,
        8.4,
        380,
        "it",
    ),
    3111: film(
        3111,
        "Le Cercle Rouge",
        "1970-10-20",
        ["Crime", "Drama"],
        "Jean-Pierre Melville",
        4.0,
        7.9,
        190,
        "fr",
    ),
    5925: film(
        5925,
        "The Great Escape",
        "1963-07-04",
        ["Action", "War"],
        "John Sturges",
        11.0,
        8.0,
        510,
    ),
    429: film(
        429,
        "The Good, the Bad and the Ugly",
        "1966-12-23",
        ["Western"],
        "Sergio Leone",
        14.0,
        8.5,
        620,
    ),
}

LIST_FIELDS = (
    "id",
    "title",
    "release_date",
    "overview",
    "poster_path",
    "popularity",
    "genre_ids",
    "original_language",
    "vote_average",
    "vote_count",
)
"""What every list response carries: a row is the film minus its detail-only fields."""


def listed(entries: Any) -> dict[str, Any]:
    return {"page": 1, "results": [{key: entry[key] for key in LIST_FIELDS} for entry in entries]}


def neighbours(tmdb_id: int) -> list[dict[str, Any]]:
    """What TMDB would call near a film: everything sharing a genre with it, minus itself.

    Crude on purpose. The feed's job is to be selective and the prefilter and reranker are
    what do the selecting, so a fake that answered cleverly would be doing their work and
    hiding whether they do it.
    """
    entry = CATALOG.get(tmdb_id)
    if entry is None:
        return []
    genres = set(entry["genre_ids"])
    return [
        other
        for other in CATALOG.values()
        if other["id"] != tmdb_id and genres.intersection(other["genre_ids"])
    ]


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        url = urlparse(self.path)
        path = url.path.removeprefix("/3")
        query = parse_qs(url.query)
        if path == "/search/movie":
            wanted = (query.get("query") or [""])[0].casefold()
            hits = [entry for entry in CATALOG.values() if wanted in entry["title"].casefold()]
            return self._json(200, listed(hits))
        if path in ("/movie/popular", "/movie/top_rated"):
            # The warmup's "need inspiration?" fallback. Ranked apart so the dev stack
            # shows two grids rather than one list under two names.
            key = "popularity" if path.endswith("popular") else "vote_average"
            ranked = sorted(CATALOG.values(), key=lambda entry: -float(entry[key]))
            return self._json(200, listed(ranked))
        if path == "/genre/movie/list":
            return self._json(
                200, {"genres": [{"id": id, "name": name} for name, id in GENRE_IDS.items()]}
            )
        if path == "/discover/movie":
            return self._json(200, listed(_steered(query)))
        if path.endswith(("/similar", "/recommendations")):
            return self._json(200, listed(neighbours(_tmdb_id(path))))
        if path.startswith("/movie/"):
            entry = CATALOG.get(_tmdb_id(path))
            if entry is None:
                return self._json(404, {"status_message": "The resource could not be found."})
            return self._json(200, entry)
        self._json(404, {"status_message": "The resource could not be found."})

    def _json(self, status: int, body: Any) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: Any) -> None:
        pass


def _steered(query: dict[str, list[str]]) -> list[dict[str, Any]]:
    """The catalog through one discover slice's filters, most popular first, as TMDB ranks.

    The filters are answered rather than ignored: a slice is a question pointed somewhere,
    and a fake that returned everything would let the pipeline steer at the wrong feature
    and still look like it worked.
    """
    genre = query.get("with_genres")
    person = query.get("with_people")
    floor = int((query.get("vote_count.gte") or ["0"])[0])
    found = []
    for entry in CATALOG.values():
        if genre and int(genre[0]) not in entry["genre_ids"]:
            continue
        if person and int(person[0]) not in _people_ids(entry):
            continue
        if entry["vote_count"] < floor:
            continue
        found.append(entry)
    return sorted(found, key=lambda entry: -float(entry["popularity"]))


def _people_ids(entry: dict[str, Any]) -> list[int]:
    credits = entry["credits"]
    return [person["id"] for person in credits["cast"] + credits["crew"]]


def _tmdb_id(path: str) -> int:
    try:
        return int(path.removeprefix("/movie/").split("/")[0])
    except ValueError:
        return 0


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8030"))
    print(f"fake TMDB listening on :{port}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
