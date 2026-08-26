from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from anchor import health, jobs
from anchor.db import Database
from anchor.settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        app.state.db = Database(settings)
        app.state.jobs = jobs.build_app(settings)
        async with app.state.jobs.open_async():
            try:
                yield
            finally:
                await app.state.db.dispose()

    app = FastAPI(title="Anchor", lifespan=lifespan)
    app.include_router(health.router)
    return app


app = create_app()
