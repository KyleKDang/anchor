"""The worker process: ``python -m anchor.worker``."""

import asyncio
import logging

from anchor import jobs
from anchor.db import Database
from anchor.settings import Settings


async def run(settings: Settings) -> None:
    db = Database(settings)
    app = jobs.build_app(settings)
    try:
        async with app.open_async():
            await app.run_worker_async(additional_context=jobs.worker_context(db))
    finally:
        await db.dispose()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run(Settings()))


if __name__ == "__main__":
    main()
