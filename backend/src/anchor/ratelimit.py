"""Per-IP rate limits on the unauthenticated auth endpoints.

A sliding window kept in process memory: the app runs as one web process on one box,
and a limit that resets on restart costs nothing. The client IP is what uvicorn reports
after honouring Caddy's forwarded headers (see the compose file).
"""

from collections import deque
from collections.abc import Callable
from time import monotonic

from fastapi import Request, params

from anchor.errors import ApiError
from anchor.settings import Settings

SWEEP_EVERY = 1000
"""Hits between sweeps of idle keys, so the table stays bounded by recently active IPs."""


class RateLimiter:
    def __init__(self, window_seconds: float) -> None:
        self.window = window_seconds
        self._hits: dict[tuple[str, str], deque[float]] = {}
        self._since_sweep = 0

    def hit(self, scope: str, key: str, limit: int) -> float | None:
        """Record a hit; return the seconds to wait if it went over the limit, else None."""
        now = monotonic()
        hits = self._hits.setdefault((scope, key), deque())
        self._expire(hits, now)
        self._maybe_sweep(now)
        if len(hits) >= limit:
            return hits[0] + self.window - now
        hits.append(now)
        return None

    def _expire(self, hits: deque[float], now: float) -> None:
        while hits and hits[0] <= now - self.window:
            hits.popleft()

    def _maybe_sweep(self, now: float) -> None:
        self._since_sweep += 1
        if self._since_sweep < SWEEP_EVERY:
            return
        self._since_sweep = 0
        for key, hits in list(self._hits.items()):
            self._expire(hits, now)
            if not hits:
                del self._hits[key]


def limited(scope: str, limit_of: Callable[[Settings], int]) -> params.Depends:
    """A route dependency: at most ``limit_of(settings)`` hits per IP per window."""

    async def dependency(request: Request) -> None:
        settings: Settings = request.app.state.settings
        limiter: RateLimiter = request.app.state.rate_limiter
        ip = request.client.host if request.client else "unknown"
        retry_after = limiter.hit(scope, ip, limit_of(settings))
        if retry_after is not None:
            raise ApiError(
                429,
                "rate_limited",
                "Too many attempts from your network; try again in a few minutes.",
                headers={"Retry-After": str(max(1, int(retry_after) + 1))},
            )

    return params.Depends(dependency)
