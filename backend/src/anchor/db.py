from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from anchor.settings import Settings


class Base(DeclarativeBase):
    pass


class Database:
    """The process's connection to PostgreSQL: one engine, a session factory."""

    def __init__(self, settings: Settings) -> None:
        self.engine: AsyncEngine = create_async_engine(settings.sqlalchemy_url)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def dispose(self) -> None:
        await self.engine.dispose()
