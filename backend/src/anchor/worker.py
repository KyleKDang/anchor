"""The worker process: ``python -m anchor.worker``."""

import asyncio
import logging

from anchor import jobs, sentry, tmdb
from anchor.db import Database
from anchor.settings import Settings


async def run(settings: Settings) -> None:
    db = Database(settings)
    catalog_client = tmdb.build_tmdb(settings)
    app = jobs.build_app(settings)
    try:
        async with app.open_async():
            await app.run_worker_async(
                additional_context=jobs.worker_context(db, catalog_client, settings)
            )
    finally:
        await catalog_client.aclose()
        await db.dispose()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = Settings()
    sentry.install(settings)
    asyncio.run(run(settings))


if __name__ == "__main__":
    main()
