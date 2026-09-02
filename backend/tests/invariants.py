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


NO_RATING_KEYS = frozenset({"rating", "stars", "band", "score", "predicted_rating"})
"""Mid-flow, a rating-shaped key must be absent, not merely empty."""


def assert_no_rating_keys(payload: Any, where: str = "response") -> None:
    """Ratings are hidden mid-flow, so the value never leaves the server at all.

    Stronger than :func:`assert_nothing_rating_shaped`: a null ``rating`` is an honest
    statement that no rating exists, but during a comparison even the key is a leak
    waiting to happen, and the owner must answer uncontaminated by the opponent's band.
    """
    for key, _ in _entries(payload):
        assert key not in NO_RATING_KEYS, f"{where} carries a rating-shaped key: {key}"


# --- The ordering ---


async def ordering_snapshot(db: Database, account_id: uuid.UUID) -> list[list[int]]:
    """The account's ordering as plain film ids, best slot first, for before/after checks."""
    async with db.sessions() as session:
        rows = await session.execute(
            text(
                """
                SELECT s.position, af.film_id
                FROM tie_group_slots s
                JOIN placements p ON p.slot_id = s.id
                JOIN account_films af ON af.id = p.account_film_id
                WHERE s.account_id = :id
                ORDER BY s.position, af.film_id
                """
            ),
            {"id": account_id},
        )
    slots: dict[int, list[int]] = {}
    for position, film_id in rows:
        slots.setdefault(position, []).append(film_id)
    return [slots[position] for position in sorted(slots)]


async def assert_ordering_well_formed(db: Database, account_id: uuid.UUID) -> None:
    """The ordering says what ADR 0001 says it says, after any flow that touched it.

    Positions are dense and start at 0, no slot sits empty, rated and placed mean each
    other, and the films sharing a slot are connected by explicit tie judgments - so no
    film is ever silently asserted equal to one it was never compared with.
    """
    async with db.sessions() as session:
        positions = list(
            await session.scalars(
                text(
                    "SELECT position FROM tie_group_slots WHERE account_id = :id ORDER BY position"
                ),
                {"id": account_id},
            )
        )
        assert positions == list(range(len(positions))), positions

        rated = set(
            await session.scalars(
                text(
                    "SELECT film_id FROM account_films WHERE account_id = :id AND state = 'rated'"
                ),
                {"id": account_id},
            )
        )
        placed = list(
            await session.scalars(
                text(
                    """
                    SELECT af.film_id FROM placements p
                    JOIN account_films af ON af.id = p.account_film_id
                    WHERE p.account_id = :id
                    """
                ),
                {"id": account_id},
            )
        )
        assert sorted(placed) == sorted(rated), (placed, sorted(rated))
        assert len(placed) == len(set(placed)), placed

        ties = [
            (row[0], row[1])
            for row in await session.execute(
                text(
                    """
                    SELECT film_a_id, film_b_id FROM comparison_log_entries
                    WHERE account_id = :id AND verdict = 'tied' AND status = 'active'
                    """
                ),
                {"id": account_id},
            )
        ]

    for slot in await ordering_snapshot(db, account_id):
        assert slot, "a slot never sits empty"
        _assert_tie_connected(slot, ties)


def _assert_tie_connected(slot: list[int], ties: list[tuple[int, int]]) -> None:
    """Every member of a slot reaches every other through recorded tie judgments."""
    members = set(slot)
    reached = {slot[0]}
    frontier = [slot[0]]
    while frontier:
        film = frontier.pop()
        for a, b in ties:
            for near, far in ((a, b), (b, a)):
                if near == film and far in members and far not in reached:
                    reached.add(far)
                    frontier.append(far)
    assert reached == members, f"slot {slot} is not connected by tie judgments"


# --- The comparison log ---


async def comparison_log(db: Database, account_id: uuid.UUID) -> list[tuple[Any, ...]]:
    """Every logged judgment, oldest first, as comparable tuples."""
    async with db.sessions() as session:
        rows = await session.execute(
            text(
                """
                SELECT id, kind, subject_film_id, film_a_id, film_b_id, verdict, context,
                       status, created_at
                FROM comparison_log_entries WHERE account_id = :id
                ORDER BY created_at, id
                """
            ),
            {"id": account_id},
        )
        return [tuple(row) for row in rows]


def assert_appended_only(
    before: list[tuple[Any, ...]], after: list[tuple[Any, ...]], where: str = "the flow"
) -> None:
    """The log only ever grows: ADR 0010's append-only rule, checked across a flow.

    Nothing before is deleted and nothing before is rewritten, so ``before`` must still
    be the front of ``after`` exactly as it was.
    """
    assert after[: len(before)] == before, f"{where} rewrote the comparison log"
