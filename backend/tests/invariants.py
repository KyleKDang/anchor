"""Shared assertion helpers for the cross-cutting invariants of data-model.md.

Run after mutating flows: every account-realm row is owner-scoped, an account's realm
is exactly what its owner did (empty for an unverified account, gone after deletion),
and nothing rating-shaped ever reaches a surface showing an unwatched film.
"""

import uuid
from collections.abc import Iterator
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from anchor.db import Database

# The account record itself: the one row an unverified account may have.
ACCOUNT_TABLE = "accounts"


async def account_realm_tables(session: AsyncSession) -> list[str]:
    """Every table owned by an account: the ones carrying an ``account_id`` column."""
    rows = await session.execute(
        text(
            """
            SELECT table_name FROM information_schema.columns
            WHERE table_schema = current_schema() AND column_name = 'account_id'
            ORDER BY table_name
            """
        )
    )
    return [row[0] for row in rows]


async def realm_row_counts(db: Database, account_id: uuid.UUID) -> dict[str, int]:
    """Rows per account-realm table owned by ``account_id`` (the account record excluded)."""
    async with db.sessions() as session:
        counts = {}
        for table in await account_realm_tables(session):
            counts[table] = await session.scalar(
                text(f'SELECT count(*) FROM "{table}" WHERE account_id = :id'),
                {"id": account_id},
            )
        return counts


async def account_exists(db: Database, account_id: uuid.UUID) -> bool:
    async with db.sessions() as session:
        return bool(
            await session.scalar(
                text(f'SELECT count(*) FROM "{ACCOUNT_TABLE}" WHERE id = :id'), {"id": account_id}
            )
        )


async def assert_realm_empty(db: Database, account_id: uuid.UUID) -> None:
    """No rows beyond the account record: the unverified-inert invariant."""
    counts = await realm_row_counts(db, account_id)
    assert all(count == 0 for count in counts.values()), counts


async def assert_realm_wiped(db: Database, account_id: uuid.UUID) -> None:
    """Neither the account record nor any row of its realm remains."""
    assert not await account_exists(db, account_id)
    await assert_realm_empty(db, account_id)


RATING_SHAPED_KEYS = frozenset({"rating", "stars", "band", "score", "predicted_rating"})
"""Keys that would carry a rating-shaped value, whatever the surface calls it."""


def assert_nothing_rating_shaped(payload: Any, where: str = "response") -> None:
    """ADR 0005: no rating-shaped value may appear anywhere for an unwatched film.

    Absence is fine - a ``rating`` key sitting at null states that no rating exists,
    which is the honest answer. A *value* under one of these keys is the violation.
    """
    for key, value in _entries(payload):
        assert not (key in RATING_SHAPED_KEYS and value is not None), (
            f"{where} carries a rating-shaped value: {key}={value!r}"
        )


def _entries(node: Any) -> Iterator[tuple[str, Any]]:
    if isinstance(node, dict):
        for key, value in node.items():
            yield str(key), value
            yield from _entries(value)
    elif isinstance(node, list):
        for item in node:
            yield from _entries(item)
