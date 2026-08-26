"""The API-seam harness: the FastAPI app over a throwaway real PostgreSQL.

Every test gets a fresh database cloned from a migrated template, the app
booted against it, and the queue app for enqueueing and running jobs inline.
"""

import asyncio
import json
import os
import re
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import procrastinate
import psycopg
import pytest
from alembic import command
from alembic.config import Config
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from procrastinate.worker import Worker

from anchor import jobs
from anchor.db import Database
from anchor.main import create_app
from anchor.settings import Settings

ADMIN_URL = os.environ.get(
    "ANCHOR_TEST_ADMIN_DATABASE_URL", "postgresql://anchor:anchor@localhost:5433/postgres"
)
HERE = os.path.dirname(os.path.abspath(__file__))
ALEMBIC_INI = os.path.join(HERE, "..", "alembic.ini")


def _url_for(database: str) -> str:
    return ADMIN_URL.rsplit("/", 1)[0] + "/" + database


def _admin(sql: str) -> None:
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        conn.execute(sql)


@pytest.fixture(scope="session")
def template_database() -> Iterator[str]:
    """A migrated database used as the template for every test's database."""
    name = f"anchor_test_template_{uuid.uuid4().hex[:8]}"
    _admin(f'CREATE DATABASE "{name}"')
    try:
        migrate(_url_for(name), "head")
        yield name
    finally:
        _admin(f'DROP DATABASE "{name}"')


def migrate(database_url: str, revision: str) -> None:
    config = Config(ALEMBIC_INI)
    config.set_main_option("sqlalchemy.url", Settings(database_url=database_url).sqlalchemy_url)
    if revision == "base":
        command.downgrade(config, revision)
    else:
        command.upgrade(config, revision)


@pytest.fixture
def settings(request: pytest.FixtureRequest, template_database: str) -> Iterator[Settings]:
    """Settings for a fresh database; ``@pytest.mark.settings(...)`` overrides fields."""
    marker = request.node.get_closest_marker("settings")
    overrides = dict(marker.kwargs) if marker else {}
    name = f"anchor_test_{uuid.uuid4().hex[:8]}"
    _admin(f'CREATE DATABASE "{name}" TEMPLATE "{template_database}"')
    try:
        yield Settings(database_url=_url_for(name), **overrides)
    finally:
        _admin(f'DROP DATABASE "{name}" WITH (FORCE)')


@dataclass
class FakeResend:
    """Resend faked at its HTTP edge: records every message the app posts to it."""

    sent: list[dict[str, Any]] = field(default_factory=list)
    down: bool = False
    """When set, Resend answers every send with a 500."""

    def transport(self) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            assert request.url == "https://api.resend.com/emails"
            assert request.headers["authorization"].startswith("Bearer ")
            if self.down:
                return httpx.Response(500, json={"message": "resend is down"})
            self.sent.append(json.loads(request.content))
            return httpx.Response(200, json={"id": str(uuid.uuid4())})

        return httpx.MockTransport(handle)

    def sent_to(self, email: str) -> list[dict[str, Any]]:
        return [message for message in self.sent if message["to"] == [email]]

    def verification_token(self, email: str) -> str:
        """The token in the latest verification link mailed to ``email``."""
        message = self.sent_to(email)[-1]
        match = re.search(r"/verify\?token=([A-Za-z0-9_-]+)", message["text"])
        assert match, message["text"]
        return match.group(1)


@pytest.fixture
def resend() -> FakeResend:
    return FakeResend()


@pytest.fixture
async def app(settings: Settings, resend: FakeResend) -> AsyncIterator[FastAPI]:
    app = create_app(settings, resend_transport=resend.transport())
    async with LifespanManager(app):
        yield app


@pytest.fixture
def client_from(app: FastAPI) -> Callable[[str], AsyncClient]:
    """A client whose requests arrive from the given IP address."""

    def make(ip: str) -> AsyncClient:
        transport = ASGITransport(app=app, client=(ip, 12345))
        return AsyncClient(transport=transport, base_url="https://test")

    return make


@pytest.fixture
async def client(client_from: Callable[[str], AsyncClient]) -> AsyncIterator[AsyncClient]:
    async with client_from("127.0.0.1") as client:
        yield client


@pytest.fixture
def db(app: FastAPI) -> Database:
    return app.state.db


@pytest.fixture
def jobs_app(app: FastAPI) -> procrastinate.App:
    return app.state.jobs


@pytest.fixture
async def worker(jobs_app: procrastinate.App, db: Database) -> AsyncIterator[None]:
    """A real worker on the test's event loop, as the worker process would run."""
    worker = Worker(
        jobs_app, install_signal_handlers=False, additional_context=jobs.worker_context(db)
    )
    task = asyncio.create_task(worker.run())
    try:
        yield
    finally:
        worker.stop()
        await task


@pytest.fixture
def run_jobs(jobs_app: procrastinate.App, db: Database) -> Callable[[], Awaitable[None]]:
    """``await run_jobs()`` executes every queued job inline in the test, then returns."""

    async def run() -> None:
        await jobs_app.run_worker_async(
            wait=False,
            install_signal_handlers=False,
            listen_notify=False,
            additional_context=jobs.worker_context(db),
        )

    return run
