"""Letterboxd's public site, faked at the HTTP edge, for the per-row rescue.

The chain the rescue walks was verified live (#16, and the research note): a boxd.it
short link redirects to the film page, and that page's body carries ``data-tmdb-id`` and
``data-tmdb-type`` attributes. None of it is an API and none of it was promised, so the
fake can serve the three answers that matter - the film page, a TV-side entry, and the
403 Letterboxd hands anything that does not look like a browser.
"""

from dataclasses import dataclass, field

import httpx

FILM_PAGE = """<!DOCTYPE html><html><head><title>{title}</title></head>
<body class="film" data-tmdb-id="{tmdb_id}" data-tmdb-type="{kind}">
<h1>{title}</h1></body></html>"""


@dataclass
class FakeLetterboxd:
    """A handful of resolvable links, and the failure modes the rescue must tolerate."""

    films: dict[str, int] = field(default_factory=dict)
    """boxd.it link to the TMDB *movie* id its film page carries."""
    series: dict[str, int] = field(default_factory=dict)
    """Links whose page names a TV entry, which is no film to bind."""
    forbidden: bool = False
    """Letterboxd answers 403 to fetchers it does not like; observed live, 2026-07-26."""
    requests: list[httpx.Request] = field(default_factory=list)

    def resolving(self, uri: str, tmdb_id: int) -> "FakeLetterboxd":
        self.films[uri] = tmdb_id
        return self

    def as_series(self, uri: str, tmdb_id: int) -> "FakeLetterboxd":
        self.series[uri] = tmdb_id
        return self

    def transport(self) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            if self.forbidden:
                return httpx.Response(403, text="no")
            uri = str(request.url)
            if uri in self.films:
                return httpx.Response(
                    200,
                    html=FILM_PAGE.format(title="A Film", tmdb_id=self.films[uri], kind="movie"),
                )
            if uri in self.series:
                return httpx.Response(
                    200,
                    html=FILM_PAGE.format(title="A Series", tmdb_id=self.series[uri], kind="tv"),
                )
            return httpx.Response(404, text="not found")

        return httpx.MockTransport(handle)
