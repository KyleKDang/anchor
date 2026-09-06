"""The shared TMDB client: one bundled call per film, self-throttled, 429-aware.

Anchor's whole catalog arrives through here, and two rules from ADR 0003 live in
this module. A film costs exactly one HTTP call - ``append_to_response`` folds
credits and keywords into the detail request - and only image *paths* are ever
taken, so the bytes stay on TMDB's CDN and the browser hotlinks them.

The client is shared process-wide and spaces its requests a few per second, far
under TMDB's ~40/s soft limit, so no burst of film pages can trip it; a 429 anyway
is waited out and retried. ``clock`` and ``sleep`` are injected so tests can
exercise the spacing without spending the time, and the HTTP transport is the
fake boundary (testing.md).
"""

import asyncio
import enum
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from time import monotonic
from typing import Any, Protocol

import httpx

from anchor.settings import Settings

APPENDED = "credits,keywords"
"""Folded into the film detail call, which is what makes one film exactly one request."""

TOP_CAST = 10
"""Billing positions kept; the tail is noise for both display and the recommender."""

DEFAULT_RETRY_AFTER = 1.0
"""Waited after a 429 that names no ``Retry-After``."""

Clock = Callable[[], float]
Sleep = Callable[[float], Awaitable[None]]

UNCONFIGURED = "Film search is not configured on this server."


class TmdbUnavailable(Exception):
    """TMDB could not answer: unconfigured, down, or still throttling after every retry."""


class FilmNotInTmdb(Exception):
    """TMDB has no film under that id."""


@dataclass(frozen=True)
class SearchHit:
    """One row of any of TMDB's list responses - search, the browse grids, and discovery's
    three candidate endpoints all return the same movie object.

    The last three fields are what the discovery prefilter reads. They are on the list
    row rather than fetched per film on purpose: a restock unions a few hundred
    candidates and keeps sixty, and bundling the two hundred it throws away would be four
    hundred TMDB calls spent on films nobody will ever see.
    """

    tmdb_id: int
    title: str
    year: int | None
    overview: str
    poster_path: str | None
    popularity: float
    """TMDB's own popularity figure: what ranks the import's review candidates."""
    genre_ids: tuple[int, ...] = ()
    """Genres as ids: a list row names them numerically, where a detail call spells them out."""
    original_language: str | None = None
    vote_average: float = 0.0
    vote_count: int = 0


@dataclass(frozen=True)
class FilmBundle:
    """One film's bundled metadata, trimmed to what the shared store keeps."""

    tmdb_id: int
    title: str
    year: int | None
    overview: str
    poster_path: str | None
    backdrop_path: str | None
    runtime: int | None
    genres: list[str]
    keywords: list[str]
    credits: dict[str, Any]
    vote_average: float
    vote_count: int
    original_language: str | None


class Browse(enum.StrEnum):
    """The two grids TMDB offers as a list rather than an answer to a question.

    They are the warmup's "need inspiration?" fallback and nothing more: popularity
    grids bias hard toward blockbusters, so search stays the headline act and this is
    what the owner reaches for when they cannot think of a film to name.
    """

    popular = "popular"
    top_rated = "top_rated"


@dataclass(frozen=True)
class Steer:
    """What one ``/discover`` slice is pointed at.

    The discovery pipeline builds one of these per top-weighted feature in the owner's
    fit, so a slice is always a question with a reason behind it - "more films by the
    director they keep rating up" - rather than a browse of the catalog.
    """

    genre_id: int | None = None
    person_id: int | None = None
    min_votes: int = 0
    """A floor on the vote count, which is TMDB's own sparseness signal. It keeps a slice
    from filling with rows nobody has seen; the popularity *damper* is a separate thing
    and lives in the prefilter, where deep cuts are meant to win."""


class Tmdb(Protocol):
    async def search(self, query: str) -> list[SearchHit]: ...

    async def browse(self, kind: Browse) -> list[SearchHit]: ...

    async def film(self, tmdb_id: int) -> FilmBundle: ...

    async def discover(self, steer: Steer) -> list[SearchHit]: ...

    async def similar(self, tmdb_id: int) -> list[SearchHit]: ...

    async def recommendations(self, tmdb_id: int) -> list[SearchHit]: ...

    async def genre_ids(self) -> dict[str, int]: ...

    async def aclose(self) -> None: ...


class Throttle:
    """Holds every request through the shared client at least ``interval`` apart."""

    def __init__(self, interval: float, clock: Clock, sleep: Sleep) -> None:
        self._interval = interval
        self._clock = clock
        self._sleep = sleep
        self._lock = asyncio.Lock()
        self._free_at = 0.0

    async def take(self) -> None:
        # The lock is the throttle: callers queue on it, so the spacing holds across
        # concurrent requests rather than each one measuring only against itself.
        async with self._lock:
            waiting = self._free_at - self._clock()
            if waiting > 0:
                await self._sleep(waiting)
            self._free_at = max(self._clock(), self._free_at) + self._interval


class TmdbClient:
    def __init__(
        self,
        access_token: str,
        base_url: str,
        requests_per_second: float,
        max_attempts: int,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Clock = monotonic,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            transport=transport,
            timeout=10.0,
        )
        self._throttle = Throttle(1.0 / requests_per_second, clock, sleep)
        self._max_attempts = max_attempts
        self._sleep = sleep
        self._genres: dict[str, int] | None = None

    async def search(self, query: str) -> list[SearchHit]:
        payload = await self._get("/search/movie", {"query": query, "include_adult": "false"})
        return [_hit(result) for result in payload.get("results") or []]

    async def browse(self, kind: Browse) -> list[SearchHit]:
        payload = await self._get(f"/movie/{kind}", {})
        return [_hit(result) for result in payload.get("results") or []]

    async def film(self, tmdb_id: int) -> FilmBundle:
        """The one bundled call: detail, credits, and keywords in a single request."""
        payload = await self._get(f"/movie/{tmdb_id}", {"append_to_response": APPENDED})
        return _bundle(payload)

    async def discover(self, steer: Steer) -> list[SearchHit]:
        """One candidate slice of the catalog, steered at a genre, a person, or both."""
        payload = await self._get("/discover/movie", _steered(steer))
        return [_hit(result) for result in payload.get("results") or []]

    async def similar(self, tmdb_id: int) -> list[SearchHit]:
        """TMDB's own "more like this", by shared genres and keywords."""
        payload = await self._get(f"/movie/{tmdb_id}/similar", {})
        return [_hit(result) for result in payload.get("results") or []]

    async def recommendations(self, tmdb_id: int) -> list[SearchHit]:
        """TMDB's behavioural neighbours, which overlap ``similar`` only partly."""
        payload = await self._get(f"/movie/{tmdb_id}/recommendations", {})
        return [_hit(result) for result in payload.get("results") or []]

    async def genre_ids(self) -> dict[str, int]:
        """TMDB's genre vocabulary, name to id, fetched once per process.

        Anchor stores genres by name, because that is what a film's detail call spells
        out and what the feature space is keyed on; ``/discover`` only accepts ids. The
        list is a fixed couple of dozen entries that changes about never, so it is worth
        exactly one call for the life of the process.
        """
        if self._genres is None:
            payload = await self._get("/genre/movie/list", {})
            self._genres = {
                str(genre["name"]): int(genre["id"]) for genre in payload.get("genres") or []
            }
        return self._genres

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        for attempt in range(1, self._max_attempts + 1):
            await self._throttle.take()
            try:
                response = await self._client.get(path, params=params)
            except httpx.HTTPError as error:
                raise TmdbUnavailable(f"TMDB is unreachable: {error}") from error
            if response.status_code == 404:
                raise FilmNotInTmdb(path)
            if response.status_code == 429 and attempt < self._max_attempts:
                await self._sleep(_retry_after(response))
                continue
            if response.is_error:
                raise TmdbUnavailable(f"TMDB answered {response.status_code} for {path}")
            return dict(response.json())
        raise TmdbUnavailable(f"TMDB kept throttling {path} after {self._max_attempts} attempts")


class UnconfiguredTmdb:
    """No TMDB credential: every call fails outright rather than half-working."""

    async def search(self, query: str) -> list[SearchHit]:
        raise TmdbUnavailable(UNCONFIGURED)

    async def browse(self, kind: Browse) -> list[SearchHit]:
        raise TmdbUnavailable(UNCONFIGURED)

    async def film(self, tmdb_id: int) -> FilmBundle:
        raise TmdbUnavailable(UNCONFIGURED)

    async def discover(self, steer: Steer) -> list[SearchHit]:
        raise TmdbUnavailable(UNCONFIGURED)

    async def similar(self, tmdb_id: int) -> list[SearchHit]:
        raise TmdbUnavailable(UNCONFIGURED)

    async def recommendations(self, tmdb_id: int) -> list[SearchHit]:
        raise TmdbUnavailable(UNCONFIGURED)

    async def genre_ids(self) -> dict[str, int]:
        raise TmdbUnavailable(UNCONFIGURED)

    async def aclose(self) -> None:
        pass


def build_tmdb(settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> Tmdb:
    """The real client when a token is configured or a transport is injected."""
    if transport is None and settings.tmdb_access_token is None:
        return UnconfiguredTmdb()
    return TmdbClient(
        access_token=settings.tmdb_access_token or "unset",
        base_url=settings.tmdb_base_url,
        requests_per_second=settings.tmdb_requests_per_second,
        max_attempts=settings.tmdb_max_attempts,
        transport=transport,
    )


def _retry_after(response: httpx.Response) -> float:
    try:
        return max(0.0, float(response.headers.get("Retry-After", "")))
    except ValueError:
        return DEFAULT_RETRY_AFTER


def _steered(steer: Steer) -> dict[str, str]:
    """One steer as TMDB's discover parameters. Absent steers add no parameter at all."""
    params = {"include_adult": "false"}
    if steer.genre_id is not None:
        params["with_genres"] = str(steer.genre_id)
    if steer.person_id is not None:
        params["with_people"] = str(steer.person_id)
    if steer.min_votes > 0:
        params["vote_count.gte"] = str(steer.min_votes)
    return params


def _hit(result: dict[str, Any]) -> SearchHit:
    return SearchHit(
        tmdb_id=int(result["id"]),
        title=str(result.get("title") or ""),
        year=_year(result.get("release_date")),
        overview=str(result.get("overview") or ""),
        poster_path=result.get("poster_path"),
        popularity=float(result.get("popularity") or 0.0),
        genre_ids=tuple(int(genre) for genre in result.get("genre_ids") or ()),
        original_language=result.get("original_language"),
        vote_average=float(result.get("vote_average") or 0.0),
        vote_count=int(result.get("vote_count") or 0),
    )


def _bundle(payload: dict[str, Any]) -> FilmBundle:
    credits = payload.get("credits") or {}
    crew = credits.get("crew") or []
    return FilmBundle(
        tmdb_id=int(payload["id"]),
        title=str(payload.get("title") or ""),
        year=_year(payload.get("release_date")),
        overview=str(payload.get("overview") or ""),
        poster_path=payload.get("poster_path"),
        backdrop_path=payload.get("backdrop_path"),
        runtime=payload.get("runtime"),
        genres=[str(genre["name"]) for genre in payload.get("genres") or []],
        keywords=[
            str(keyword["name"])
            for keyword in (payload.get("keywords") or {}).get("keywords") or []
        ],
        credits={
            "directors": _people(member for member in crew if member.get("job") == "Director"),
            "cast": _people((credits.get("cast") or [])[:TOP_CAST]),
        },
        vote_average=float(payload.get("vote_average") or 0.0),
        vote_count=int(payload.get("vote_count") or 0),
        original_language=payload.get("original_language"),
    )


def _people(entries: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"id": int(entry["id"]), "name": str(entry["name"])} for entry in entries]


def _year(release_date: Any) -> int | None:
    """TMDB dates are ``YYYY-MM-DD``, but an unreleased film carries ``""`` or nothing."""
    text = str(release_date or "")[:4]
    return int(text) if text.isdigit() else None
