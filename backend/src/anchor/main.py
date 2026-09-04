from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from anchor import (
    accounts,
    anchors,
    criteria,
    drift,
    errors,
    films,
    health,
    imports,
    jobs,
    letterboxd,
    mail,
    placement,
    profile,
    rated,
    rewatch,
    sentry,
    tmdb,
    warmup,
    watchlist,
)
from anchor.db import Database
from anchor.ratelimit import RateLimiter
from anchor.settings import Settings


def create_app(
    settings: Settings | None = None,
    *,
    resend_transport: httpx.AsyncBaseTransport | None = None,
    tmdb_transport: httpx.AsyncBaseTransport | None = None,
    letterboxd_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    """The web process. The transports are the test seams faking every outside service."""
    settings = settings or Settings()
    sentry.install(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        app.state.db = Database(settings)
        app.state.jobs = jobs.build_app(settings)
        app.state.mailer = mail.build_mailer(settings, resend_transport)
        app.state.tmdb = tmdb.build_tmdb(settings, tmdb_transport)
        app.state.letterboxd = letterboxd.Letterboxd(letterboxd_transport)
        app.state.rate_limiter = RateLimiter(settings.rate_limit_window_seconds)
        async with app.state.jobs.open_async():
            try:
                yield
            finally:
                await app.state.mailer.aclose()
                await app.state.tmdb.aclose()
                await app.state.letterboxd.aclose()
                await app.state.db.dispose()

    app = FastAPI(title="Anchor", lifespan=lifespan)
    errors.install(app)
    app.include_router(health.router)
    app.include_router(accounts.router)
    app.include_router(films.router)
    app.include_router(watchlist.router)
    app.include_router(rated.router)
    app.include_router(placement.router)
    app.include_router(anchors.router)
    app.include_router(drift.router)
    app.include_router(rewatch.router)
    app.include_router(criteria.router)
    app.include_router(profile.router)
    app.include_router(imports.router)
    app.include_router(watchlist.unlocks)
    app.include_router(warmup.router)
    return app


app = create_app()
