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
    """One row of a search response - all TMDB's search endpoint carries."""

    tmdb_id: int
    title: str
    year: int | None
    overview: str
    poster_path: str | None


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


class Tmdb(Protocol):
    async def search(self, query: str) -> list[SearchHit]: ...

    async def film(self, tmdb_id: int) -> FilmBundle: ...

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

    async def search(self, query: str) -> list[SearchHit]:
        payload = await self._get("/search/movie", {"query": query, "include_adult": "false"})
        return [_hit(result) for result in payload.get("results") or []]

    async def film(self, tmdb_id: int) -> FilmBundle:
        """The one bundled call: detail, credits, and keywords in a single request."""
        payload = await self._get(f"/movie/{tmdb_id}", {"append_to_response": APPENDED})
        return _bundle(payload)

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

    async def film(self, tmdb_id: int) -> FilmBundle:
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


def _hit(result: dict[str, Any]) -> SearchHit:
    return SearchHit(
        tmdb_id=int(result["id"]),
        title=str(result.get("title") or ""),
        year=_year(result.get("release_date")),
        overview=str(result.get("overview") or ""),
        poster_path=result.get("poster_path"),
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
    )


def _people(entries: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"id": int(entry["id"]), "name": str(entry["name"])} for entry in entries]


def _year(release_date: Any) -> int | None:
    """TMDB dates are ``YYYY-MM-DD``, but an unreleased film carries ``""`` or nothing."""
    text = str(release_date or "")[:4]
    return int(text) if text.isdigit() else None
