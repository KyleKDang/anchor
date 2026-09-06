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
from anchor.models import BUILT_IN_QUALITIES

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


async def ordering_snapshot(db: Database, account_id: uuid.UUID) -> dict[float, list[int]]:
    """The account's ordering as plain film ids per band, rank order, for before/after checks."""
    async with db.sessions() as session:
        rows = await session.execute(
            text(
                """
                SELECT p.band, af.film_id
                FROM placements p
                JOIN account_films af ON af.id = p.account_film_id
                WHERE p.account_id = :id
                ORDER BY p.band DESC, p.rank
                """
            ),
            {"id": account_id},
        )
    rows_by_band: dict[float, list[int]] = {}
    for band, film_id in rows:
        rows_by_band.setdefault(float(band), []).append(film_id)
    return rows_by_band


async def assert_ordering_well_formed(db: Database, account_id: uuid.UUID) -> None:
    """The ordering says what ADR 0001 and ADR 0013 say it says, after any flow.

    Ranks are dense from 1 inside every band, every band is one of the ten half-star
    values, rated and placed mean each other, and an anchor mark only ever sits on a
    rated film - which, with the band on the same row, is the whole of "an anchor is
    always in the band it was marked in" (data-model.md).
    """
    async with db.sessions() as session:
        rows = list(
            await session.execute(
                text(
                    """
                    SELECT band, rank FROM placements
                    WHERE account_id = :id ORDER BY band DESC, rank
                    """
                ),
                {"id": account_id},
            )
        )
        ranks: dict[float, list[int]] = {}
        for band, rank in rows:
            assert float(band) in BANDS, f"not a half-star band: {band}"
            ranks.setdefault(float(band), []).append(rank)
        for band, seen in ranks.items():
            assert seen == list(range(1, len(seen) + 1)), f"band {band} has gappy ranks: {seen}"

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

        stray = list(
            await session.scalars(
                text(
                    """
                    SELECT af.film_id FROM placements p
                    JOIN account_films af ON af.id = p.account_film_id
                    WHERE p.account_id = :id
                      AND p.anchored_at IS NOT NULL AND af.state <> 'rated'
                    """
                ),
                {"id": account_id},
            )
        )
        assert stray == [], f"an anchor mark on an unrated film: {stray}"


# --- The comparison log ---


async def quality_list(db: Database, account_id: uuid.UUID) -> list[str]:
    """The account's quality list in list order: what the criteria rotation walks."""
    async with db.sessions() as session:
        rows = await session.execute(
            text(
                """
                SELECT name FROM quality_list_entries WHERE account_id = :id
                ORDER BY position, created_at, id
                """
            ),
            {"id": account_id},
        )
        return [row[0] for row in rows]


async def quality_tags(db: Database, film_id: int) -> list[str]:
    """The shared tags on one film, in the vocabulary's own order. No account scopes it."""
    async with db.sessions() as session:
        rows = await session.execute(
            text("SELECT quality FROM quality_tags WHERE film_id = :film"), {"film": film_id}
        )
        found = {row[0] for row in rows}
    return [name for name in BUILT_IN_QUALITIES if name in found]


async def tagged_films(db: Database) -> list[int]:
    """Every film anybody has paid to have tagged, including those tagged with nothing."""
    async with db.sessions() as session:
        rows = await session.execute(
            text("SELECT tmdb_id FROM films WHERE tagged_at IS NOT NULL ORDER BY tmdb_id")
        )
        return [row[0] for row in rows]


async def criteria_log(db: Database, account_id: uuid.UUID) -> list[tuple[Any, ...]]:
    """Every criteria offer, oldest first, as (quality, film a, film b, verdict).

    An offer reading ``skip`` is one the owner never engaged with - dismissed, or simply
    left alone, which the spec requires be recorded identically.
    """
    async with db.sessions() as session:
        rows = await session.execute(
            text(
                """
                SELECT quality_list_entries.name, film_a_id, film_b_id, verdict
                FROM comparison_log_entries
                JOIN quality_list_entries ON quality_list_entries.id = quality_id
                WHERE comparison_log_entries.account_id = :id
                  AND comparison_log_entries.kind = 'criteria'
                ORDER BY comparison_log_entries.created_at, comparison_log_entries.id
                """
            ),
            {"id": account_id},
        )
        return [tuple(row) for row in rows]


async def comparison_log(db: Database, account_id: uuid.UUID) -> list[tuple[Any, ...]]:
    """Every logged judgment, oldest first, as comparable tuples."""
    async with db.sessions() as session:
        rows = await session.execute(
            text(
                """
                SELECT id, kind, subject_film_id, film_a_id, film_b_id, verdict, band,
                       context, created_at
                FROM comparison_log_entries WHERE account_id = :id
                ORDER BY created_at, id
                """
            ),
            {"id": account_id},
        )
        return [tuple(row) for row in rows]


VERDICT_COLUMN = 5
"""Where ``verdict`` sits in a :func:`comparison_log` tuple: the log's one mutable column.

There used to be two. The status column is gone with the design that wrote it (ADR 0013):
a judgment the ordering has since been moved past is read against the ordering as it
stands rather than flagged, so nothing rewrites a row to say so.
"""

# (kind, verdict) -> the verdicts that row is allowed to move to, ever.
_ANSWERABLE = {("criteria", "skip"): {"a", "b", "tied"}}
"""The log's one legal in-place change besides ``status``.

A criteria offer is written when the card is shown, reading ``skip``, because the offer
itself is the record the adaptive frequency counts and an ignored card must be recorded
identically to a dismissed one. The owner's answer arrives afterwards or never, so it
fills that row in rather than appending a second: one offer is one record. It happens at
most once per row - answering again is refused - and no other field ever moves.
"""


def assert_appended_only(
    before: list[tuple[Any, ...]], after: list[tuple[Any, ...]], where: str = "the flow"
) -> None:
    """The log only ever grows, and one column is ever rewritten (ADR 0010, ADR 0013).

    Nothing before is deleted, and every row that already existed comes back saying
    exactly what it said. The one exception is documented at :data:`_ANSWERABLE`: a
    criteria offer being answered, once. What was *judged* is what may never change.
    """
    assert len(after) >= len(before), f"{where} deleted from the comparison log"
    for was, now in zip(before, after[: len(before)], strict=True):
        if was == now:
            continue
        allowed = _ANSWERABLE.get((was[1], was[VERDICT_COLUMN]), set())
        assert _but_answer(was) == _but_answer(now) and now[VERDICT_COLUMN] in allowed, (
            f"{where} rewrote the comparison log: {was} became {now}"
        )


def _but_answer(row: tuple[Any, ...]) -> tuple[Any, ...]:
    """The row with the mutable column gone: everything that may never change."""
    return row[:VERDICT_COLUMN] + row[VERDICT_COLUMN + 1 :]


# --- Bands and anchors ---

BANDS = (5.0, 4.5, 4.0, 3.5, 3.0, 2.5, 2.0, 1.5, 1.0, 0.5)


async def anchors(db: Database, account_id: uuid.UUID) -> dict[float, list[int]]:
    """Each band's anchor pool, most recently marked first: the before/after snapshot."""
    async with db.sessions() as session:
        rows = await session.execute(
            text(
                """
                SELECT p.band, af.film_id FROM placements p
                JOIN account_films af ON af.id = p.account_film_id
                WHERE p.account_id = :id AND p.anchored_at IS NOT NULL
                ORDER BY p.band DESC, p.anchored_at DESC, af.film_id
                """
            ),
            {"id": account_id},
        )
    pools: dict[float, list[int]] = {}
    for band, film_id in rows:
        pools.setdefault(float(band), []).append(film_id)
    return pools


# --- The taste profile ---


async def weight_vector(db: Database, account_id: uuid.UUID) -> dict[str, Any] | None:
    """The account's current fit, or None where no retrain has run for it yet."""
    async with db.sessions() as session:
        row = (
            await session.execute(
                text(
                    """
                    SELECT weights, space, training_pairs FROM weight_vectors
                    WHERE account_id = :id
                    """
                ),
                {"id": account_id},
            )
        ).first()
    return {"weights": row[0], "space": row[1], "training_pairs": row[2]} if row else None


async def trained_at(db: Database, account_id: uuid.UUID) -> Any:
    """When the account's current fit was trained: the marker a retrain has to move."""
    async with db.sessions() as session:
        return await session.scalar(
            text("SELECT trained_at FROM weight_vectors WHERE account_id = :id"),
            {"id": account_id},
        )


async def exemplars(db: Database, account_id: uuid.UUID) -> list[tuple[Any, ...]]:
    """The exemplar set as (role, band, rank, film), in a stable reading order."""
    async with db.sessions() as session:
        rows = await session.execute(
            text(
                """
                SELECT role, band, rank, film_id FROM exemplars
                WHERE account_id = :id ORDER BY role, rank
                """
            ),
            {"id": account_id},
        )
        return [tuple(row) for row in rows]


async def taste_metrics(db: Database, account_id: uuid.UUID) -> list[tuple[Any, ...]]:
    """Every retrain's row, oldest first: the append-only log evaluation.md specifies."""
    async with db.sessions() as session:
        rows = await session.execute(
            text(
                """
                SELECT id, held_out_accuracy, held_out_pairs, training_pairs, rated_films,
                       bands_spanned, band_comparisons, computed_at
                FROM taste_metrics WHERE account_id = :id
                ORDER BY computed_at, id
                """
            ),
            {"id": account_id},
        )
        return [tuple(row) for row in rows]


async def spend_ledger(db: Database, account_id: uuid.UUID | None = None) -> list[tuple[Any, ...]]:
    """Every LLM call this box has paid for, oldest first.

    With no account it is the whole table, shared-scope rows included, which is what the
    global cap sums; with one it is that account's rows only.
    """
    scope = "WHERE account_id = :id" if account_id is not None else ""
    async with db.sessions() as session:
        rows = await session.execute(
            text(
                f"""
                SELECT account_id, operation, model, input_tokens, output_tokens, cost_micros
                FROM spend_ledger_entries {scope}
                ORDER BY created_at, id
                """
            ),
            {"id": account_id},
        )
        return [tuple(row) for row in rows]


async def spent_micros(db: Database, account_id: uuid.UUID | None = None) -> int:
    """What the cap check would read: month-to-date spend, in millionths of a dollar."""
    return sum(row[5] for row in await spend_ledger(db, account_id))


async def prose_versions(db: Database, account_id: uuid.UUID) -> list[tuple[Any, ...]]:
    """Every prose regeneration, oldest first. The version numbers are the point."""
    async with db.sessions() as session:
        rows = await session.execute(
            text(
                """
                SELECT version, text, trigger, placements, judgments, anchors, constraints
                FROM prose_profile_versions WHERE account_id = :id
                ORDER BY version
                """
            ),
            {"id": account_id},
        )
        return [tuple(row) for row in rows]


async def profile_constraints(db: Database, account_id: uuid.UUID) -> list[tuple[Any, ...]]:
    """Every constraint the account has ever stated, oldest first, lifted ones included.

    Lifted rows are in deliberately: the design's claim is that taking a correction back
    lifts it rather than deleting it, and a reader that only returned live rows could not
    tell the difference.
    """
    async with db.sessions() as session:
        rows = await session.execute(
            text(
                """
                SELECT c.kind, q.name, c.content, c.lifted_at IS NULL
                FROM profile_constraints c
                LEFT JOIN quality_list_entries q ON q.id = c.quality_id
                WHERE c.account_id = :id
                ORDER BY c.created_at, c.id
                """
            ),
            {"id": account_id},
        )
        return [tuple(row) for row in rows]


def assert_versions_monotonic(versions: list[tuple[Any, ...]]) -> None:
    """Version numbers start at one and step by one: the key discovery caches against."""
    numbers = [row[0] for row in versions]
    assert numbers == list(range(1, len(numbers) + 1)), numbers


async def assert_readiness_not_stored(db: Database) -> None:
    """Readiness is derived on every read, so there is no column for it to go stale in.

    Structural rather than behavioural on purpose: the invariant is about what may
    *exist*, and a column added in good faith by a later ticket is exactly the way a
    derived classification quietly becomes a stored one.
    """
    async with db.sessions() as session:
        columns = list(
            await session.scalars(
                text(
                    """
                    SELECT table_name || '.' || column_name FROM information_schema.columns
                    WHERE table_schema = current_schema() AND column_name LIKE '%readiness%'
                    """
                )
            )
        )
    assert columns == [], columns


# --- Bands ---


async def bands_reported(db: Database, account_id: uuid.UUID, reported: dict[int, float]) -> None:
    """Every band a surface showed is the band stored on that film's placement.

    Which is the whole check now: the rating is the owner's chosen band, written down,
    so a surface showing anything else is showing something it made up (ADR 0013).
    """
    stored = {
        film_id: band
        for band, films in (await ordering_snapshot(db, account_id)).items()
        for film_id in films
    }
    assert reported == stored, (reported, stored)


# --- The watch clock ---


async def watch_clock(db: Database, account_id: uuid.UUID) -> int:
    """The account's watch clock: the count of its watch events, imported ones included.

    Every cooldown and staleness measure in Anchor is denominated in this number rather
    than in calendar time, so what a seed import does to it is a behavioural fact, not
    bookkeeping - an imported back catalogue starts the clock where the owner already is.
    """
    async with db.sessions() as session:
        return (
            await session.scalar(
                text("SELECT count(*) FROM watch_events WHERE account_id = :id"),
                {"id": account_id},
            )
        ) or 0


async def watch_events(db: Database, account_id: uuid.UUID) -> list[tuple[Any, ...]]:
    """Every watch, oldest first, as (film, when, origin, rewatch)."""
    async with db.sessions() as session:
        rows = await session.execute(
            text(
                """
                SELECT film_id, watched_at, origin, rewatch FROM watch_events
                WHERE account_id = :id ORDER BY watched_at, film_id
                """
            ),
            {"id": account_id},
        )
        return [tuple(row) for row in rows]


async def watch_standings(db: Database, account_id: uuid.UUID) -> dict[int, str]:
    """Where each watched film stood on the watchlist at the moment it was watched.

    Capture-or-lose-forever (evaluation.md): tier membership keeps no history, so this is
    the only record that a watch came out of the up-next zone rather than off the backlog,
    and nothing could reconstruct it afterwards.
    """
    async with db.sessions() as session:
        rows = await session.execute(
            text("SELECT film_id, standing FROM watch_events WHERE account_id = :id"),
            {"id": account_id},
        )
    return {film_id: str(standing) for film_id, standing in rows}


async def last_synced_ratings(db: Database, account_id: uuid.UUID) -> dict[int, float]:
    """What Letterboxd holds per film, as far as Anchor knows: the sync list's baseline."""
    async with db.sessions() as session:
        rows = await session.execute(
            text(
                """
                SELECT film_id, last_synced_rating FROM account_films
                WHERE account_id = :id AND last_synced_rating IS NOT NULL
                """
            ),
            {"id": account_id},
        )
    return {film_id: rating for film_id, rating in rows}


async def placement_clocks(db: Database, account_id: uuid.UUID) -> dict[int, tuple[Any, Any]]:
    """Every placement's two timestamps as (placed_at, moved_at), keyed by film.

    ``moved_at`` is empty while the film still holds the rank the default order gave it,
    which is what makes "has the owner touched this?" a readable fact rather than a guess.
    """
    async with db.sessions() as session:
        rows = await session.execute(
            text(
                """
                SELECT af.film_id, p.placed_at, p.moved_at FROM placements p
                JOIN account_films af ON af.id = p.account_film_id
                WHERE p.account_id = :id
                """
            ),
            {"id": account_id},
        )
    return {film_id: (placed_at, moved_at) for film_id, placed_at, moved_at in rows}


# --- Discovery ---


async def verdicts(db: Database, account_id: uuid.UUID) -> list[tuple[Any, ...]]:
    """Every verdict the account holds, oldest version first.

    All versions, deliberately: the design's claim is that a version bump appends rather
    than replaces, and a reader that only returned the live ones could not tell an
    append-only cache from one that overwrites itself.
    """
    async with db.sessions() as session:
        rows = await session.execute(
            text(
                """
                SELECT film_id, profile_version, fit, explanation, rank
                FROM verdicts WHERE account_id = :id
                ORDER BY profile_version, rank, film_id
                """
            ),
            {"id": account_id},
        )
        return [tuple(row) for row in rows]


async def assert_shelf_stands_on_verdicts(db: Database, account_id: uuid.UUID) -> None:
    """The never-pad rule: every film on the shelf has a verdict, and none is a poor fit.

    Positions are dense from zero as well, because position is the entire public statement
    the feed makes (ADR 0005) and a gap in it would be the shelf saying something it does
    not mean.
    """
    async with db.sessions() as session:
        rows = list(
            await session.execute(
                text(
                    """
                    SELECT s.position, v.fit
                    FROM suggestions s JOIN verdicts v ON v.id = s.verdict_id
                    WHERE s.account_id = :id ORDER BY s.position
                    """
                ),
                {"id": account_id},
            )
        )
    assert [row[0] for row in rows] == list(range(len(rows))), rows
    assert [row[1] for row in rows if row[1] == "poor_fit"] == [], rows


async def dismiss(db: Database, account_id: uuid.UUID, film_id: int) -> None:
    """Suppress a film as the feed's own "not interested" will (#39 owns the endpoint).

    Written directly because the write path is a later ticket and the invariant is this
    one's: only untracked, undismissed films are ever suggested, whoever created the row.
    """
    async with db.sessions() as session:
        await session.execute(
            text(
                "INSERT INTO dismissals (id, account_id, film_id)"
                " VALUES (gen_random_uuid(), :account, :film)"
            ),
            {"account": account_id, "film": film_id},
        )
        await session.commit()
