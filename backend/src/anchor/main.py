from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from anchor import (
    accounts,
    anchors,
    errors,
    films,
    health,
    jobs,
    mail,
    placement,
    rated,
    sentry,
    tmdb,
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
) -> FastAPI:
    """The web process. The two transports are the test seams faking Resend and TMDB."""
    settings = settings or Settings()
    sentry.install(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        app.state.db = Database(settings)
        app.state.jobs = jobs.build_app(settings)
        app.state.mailer = mail.build_mailer(settings, resend_transport)
        app.state.tmdb = tmdb.build_tmdb(settings, tmdb_transport)
        app.state.rate_limiter = RateLimiter(settings.rate_limit_window_seconds)
        async with app.state.jobs.open_async():
            try:
                yield
            finally:
                await app.state.mailer.aclose()
                await app.state.tmdb.aclose()
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
    return app


app = create_app()
