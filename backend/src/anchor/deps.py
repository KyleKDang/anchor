"""Request-scoped access to what the app carries on its state, as FastAPI dependencies."""

from collections.abc import AsyncIterator
from typing import Annotated

import procrastinate
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from anchor.letterboxd import Letterboxd
from anchor.mail import Mailer
from anchor.ratelimit import RateLimiter
from anchor.settings import Settings
from anchor.tmdb import Tmdb


def settings_of(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


def mailer_of(request: Request) -> Mailer:
    return request.app.state.mailer  # type: ignore[no-any-return]


def tmdb_of(request: Request) -> Tmdb:
    return request.app.state.tmdb  # type: ignore[no-any-return]


def letterboxd_of(request: Request) -> Letterboxd:
    return request.app.state.letterboxd  # type: ignore[no-any-return]


def jobs_of(request: Request) -> procrastinate.App:
    return request.app.state.jobs  # type: ignore[no-any-return]


def rate_limiter_of(request: Request) -> RateLimiter:
    return request.app.state.rate_limiter  # type: ignore[no-any-return]


async def db_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.db.sessions() as session:
        yield session


AppSettings = Annotated[Settings, Depends(settings_of)]
AppJobs = Annotated[procrastinate.App, Depends(jobs_of)]
AppMailer = Annotated[Mailer, Depends(mailer_of)]
AppTmdb = Annotated[Tmdb, Depends(tmdb_of)]
AppLetterboxd = Annotated[Letterboxd, Depends(letterboxd_of)]
DbSession = Annotated[AsyncSession, Depends(db_session)]
