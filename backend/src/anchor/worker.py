"""The worker process: ``python -m anchor.worker``."""

import asyncio
import logging

from anchor import jobs, llm, sentry, tmdb
from anchor.db import Database
from anchor.settings import Settings


async def run(settings: Settings) -> None:
    # This is the one process that imports the LLM seam, and importing it here rather
    # than inside a job is the point: the rule is that the *web* process never loads it
    # (architecture.md), and the worker holding one long-lived client is what stops every
    # regeneration paying to open a connection.
    db = Database(settings)
    catalog_client = tmdb.build_tmdb(settings)
    seam = llm.build_llm(db, settings)
    app = jobs.build_app(settings)
    try:
        async with app.open_async():
            await app.run_worker_async(
                additional_context=jobs.worker_context(db, catalog_client, seam, settings)
            )
    finally:
        await seam.aclose()
        await catalog_client.aclose()
        await db.dispose()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = Settings()
    sentry.install(settings)
    asyncio.run(run(settings))


if __name__ == "__main__":
    main()
