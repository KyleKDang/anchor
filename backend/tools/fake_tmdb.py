"""A stand-in for TMDB's HTTP API, for the composed dev stack.

The shared client calls it exactly as it would call TMDB, so the dev stack has a
working search with no credential and the browser smoke suite is deterministic.
It answers the two endpoints Anchor uses - ``GET /3/search/movie`` and the bundled
``GET /3/movie/{id}?append_to_response=credits,keywords`` - over a fixed handful of
films. Standard library only. Poster paths are deliberately absent: nothing here
should hotlink real TMDB images.
"""

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse


def film(
    tmdb_id: int, title: str, release_date: str, genres: list[str], director: str
) -> dict[str, Any]:
    return {
        "id": tmdb_id,
        "title": title,
        "release_date": release_date,
        "overview": f"The plot of {title}, ending and all.",
        "poster_path": None,
        "backdrop_path": None,
        "runtime": 120,
        "genres": [{"id": 100 + i, "name": name} for i, name in enumerate(genres)],
        "vote_average": 7.5,
        "vote_count": 1000,
        "credits": {
            "cast": [{"id": 201, "name": "A Lead", "order": 0}],
            "crew": [{"id": 301, "name": director, "job": "Director"}],
        },
        "keywords": {"keywords": [{"id": 401, "name": "dev fixture"}]},
    }


# Seven films, spread across genres and decades on purpose: the Rated screen groups an
# ordering into bands and filters it, and neither shows anything worth looking at until
# the dev stack holds enough films to make more than one band and more than one decade.
CATALOG = {
    550: film(550, "Fight Club", "1999-10-15", ["Drama", "Thriller"], "David Fincher"),
    329865: film(329865, "Arrival", "2016-11-10", ["Drama", "Science Fiction"], "Denis Villeneuve"),
    949: film(949, "Heat", "1995-12-15", ["Crime", "Drama"], "Michael Mann"),
    496243: film(496243, "Parasite", "2019-05-30", ["Comedy", "Thriller"], "Bong Joon-ho"),
    244786: film(244786, "Whiplash", "2014-10-10", ["Drama", "Music"], "Damien Chazelle"),
    348: film(348, "Alien", "1979-05-25", ["Horror", "Science Fiction"], "Ridley Scott"),
    11104: film(11104, "Chungking Express", "1994-07-14", ["Drama", "Romance"], "Wong Kar-wai"),
}

SEARCH_FIELDS = ("id", "title", "release_date", "overview", "poster_path")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        url = urlparse(self.path)
        path = url.path.removeprefix("/3")
        if path == "/search/movie":
            query = (parse_qs(url.query).get("query") or [""])[0].casefold()
            hits = [entry for entry in CATALOG.values() if query in entry["title"].casefold()]
            return self._json(
                200,
                {
                    "page": 1,
                    "results": [{key: hit[key] for key in SEARCH_FIELDS} for hit in hits],
                },
            )
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


def _tmdb_id(path: str) -> int:
    try:
        return int(path.removeprefix("/movie/").split("/")[0])
    except ValueError:
        return 0


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8030"))
    print(f"fake TMDB listening on :{port}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
