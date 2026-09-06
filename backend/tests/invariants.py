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
    other, and the films sharing a slot *definitively* are connected by explicit tie
    judgments - so no film is ever silently asserted equal to one it was never compared
    with. A slot whose every member is still an untouched import seed is exempt, because
    its members are seeded equal rather than judged equal, which is the whole of what
    "provisional tie-group" means (data-model.md).
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

    seeded = await seeded_films(db, account_id)
    for slot in await ordering_snapshot(db, account_id):
        assert slot, "a slot never sits empty"
        if set(slot) <= seeded:
            continue  # seeded equal by the import, not judged equal by anybody
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
                SELECT id, kind, subject_film_id, film_a_id, film_b_id, verdict, context,
                       status, created_at
                FROM comparison_log_entries WHERE account_id = :id
                ORDER BY created_at, id
                """
            ),
            {"id": account_id},
        )
        return [tuple(row) for row in rows]


STATUS_COLUMN = 7
"""Where ``status`` sits in a :func:`comparison_log` tuple: the always-mutable column."""

VERDICT_COLUMN = 5
"""Where ``verdict`` sits: mutable once, on one kind of row, and nowhere else."""

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
    """The log only ever grows, and only two columns are ever rewritten (ADR 0010).

    Nothing before is deleted, and every row that already existed comes back saying
    exactly what it said. Status is the deliberate exception the data model names: it is
    how a judgment records that it later fell into tension or was settled against,
    without erasing that the owner made it. The second exception is far narrower and
    documented at :data:`_ANSWERABLE`: a criteria offer being answered, once. What was
    *judged* is what may never change.
    """
    assert len(after) >= len(before), f"{where} deleted from the comparison log"
    for was, now in zip(before, after[: len(before)], strict=True):
        if _but_status(was) == _but_status(now):
            continue
        allowed = _ANSWERABLE.get((was[1], was[VERDICT_COLUMN]), set())
        assert _but_answer(was) == _but_answer(now) and now[VERDICT_COLUMN] in allowed, (
            f"{where} rewrote the comparison log: {was} became {now}"
        )


def _but_status(row: tuple[Any, ...]) -> tuple[Any, ...]:
    return row[:STATUS_COLUMN] + row[STATUS_COLUMN + 1 :]


def _but_answer(row: tuple[Any, ...]) -> tuple[Any, ...]:
    """The row with both mutable columns gone: everything that may never change."""
    kept = _but_status(row)
    return kept[:VERDICT_COLUMN] + kept[VERDICT_COLUMN + 1 :]


# --- Drift ---


async def drift_flags(db: Database, account_id: uuid.UUID) -> list[tuple[Any, ...]]:
    """Every flag the account has ever carried, oldest first: film, stage, and outcome."""
    async with db.sessions() as session:
        rows = await session.execute(
            text(
                """
                SELECT af.film_id, f.stage, f.closed_at IS NULL, f.outcome
                FROM drift_flags f
                JOIN account_films af ON af.id = f.account_film_id
                WHERE f.account_id = :id
                ORDER BY f.opened_at, af.film_id
                """
            ),
            {"id": account_id},
        )
    return [tuple(row) for row in rows]


async def open_flags(db: Database, account_id: uuid.UUID) -> dict[int, str]:
    """The films carrying an open flag right now, and how loud each one is."""
    return {
        film_id: stage
        for film_id, stage, is_open, _ in await drift_flags(db, account_id)
        if is_open
    }


async def statuses(db: Database, account_id: uuid.UUID) -> dict[frozenset[int], list[str]]:
    """Every comparison's status, grouped by the pair it judged, oldest first.

    Keyed by the pair rather than by entry id so a test can say "the judgment about these
    two films is in tension" without having held on to a row it never saw created.
    """
    grouped: dict[frozenset[int], list[str]] = {}
    async with db.sessions() as session:
        rows = await session.execute(
            text(
                """
                SELECT film_a_id, film_b_id, status FROM comparison_log_entries
                WHERE account_id = :id AND kind = 'overall'
                ORDER BY created_at, id
                """
            ),
            {"id": account_id},
        )
    for film_a, film_b, status in rows:
        if film_b is not None:
            grouped.setdefault(frozenset((film_a, film_b)), []).append(status)
    return grouped


async def in_tension(db: Database, account_id: uuid.UUID) -> set[frozenset[int]]:
    """The pairs carrying a judgment that currently contradicts the ordering."""
    return {pair for pair, seen in (await statuses(db, account_id)).items() if "in_tension" in seen}


async def assert_no_drift(db: Database, account_id: uuid.UUID, where: str = "the flow") -> None:
    """Nothing flagged and nothing in tension: what a divider move has to look like."""
    flags = await drift_flags(db, account_id)
    assert flags == [], f"{where} raised a drift flag: {flags}"
    tense = await in_tension(db, account_id)
    assert tense == set(), f"{where} put judgments in tension: {tense}"


# --- Bands, dividers, and anchors ---

BANDS = (5.0, 4.5, 4.0, 3.5, 3.0, 2.5, 2.0, 1.5, 1.0, 0.5)


async def dividers(db: Database, account_id: uuid.UUID) -> dict[float, int]:
    """The account's pinned dividers, keyed by the better of the two bands each separates."""
    async with db.sessions() as session:
        rows = await session.execute(
            text("SELECT upper_band, boundary FROM dividers WHERE account_id = :id"),
            {"id": account_id},
        )
    return {upper: boundary for upper, boundary in rows}


async def anchors(db: Database, account_id: uuid.UUID) -> dict[float, int]:
    """Band to film for every current anchor: the snapshot a before/after check compares."""
    async with db.sessions() as session:
        rows = await session.execute(
            text(
                """
                SELECT d.band, af.film_id FROM anchor_designations d
                JOIN account_films af ON af.id = d.account_film_id
                WHERE d.account_id = :id AND d.status = 'current'
                """
            ),
            {"id": account_id},
        )
    return {band: film_id for band, film_id in rows}


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
                       explicit_comparisons, settled_films, bands_spanned, computed_at
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
                SELECT version, text, trigger, placements, explicit_comparisons,
                       drift_resolutions, anchors, constraints
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


# --- Bands, dividers, and anchors ---


def band_at(boundaries: dict[float, int], index: int) -> float | None:
    """The band a slot derives into, worked out here rather than asked of the code.

    Restated from data-model.md rather than imported: a film's rating is which dividers
    its position sits between, and it exists only where exactly one band fits. Dividers
    run in band order, so an unpinned one is still fenced in by the pinned ones around
    it, and a band the fence rules out is not a candidate however quiet its own divider.
    """
    possible = []
    for position, band in enumerate(BANDS):
        over = [boundaries[key] for key in BANDS[:position] if key in boundaries]
        under = [boundaries[key] for key in BANDS[position:-1] if key in boundaries]
        if over and max(over) > index:
            continue
        if under and min(under) <= index:
            continue
        possible.append(band)
    return possible[0] if len(possible) == 1 else None


async def assert_bands_derived(
    db: Database, account_id: uuid.UUID, reported: dict[int, float | None]
) -> None:
    """Every band a surface showed is the one the slots and dividers imply, and none is stored.

    The point of the check is that ``reported`` cannot have come from anywhere else:
    recomputing it from the two things that are stored has to reproduce it exactly, so a
    value written down somewhere and served from there would show up here as a mismatch.
    """
    boundaries = await dividers(db, account_id)
    ordering = await ordering_snapshot(db, account_id)
    derived = {
        film_id: band_at(boundaries, index)
        for index, slot in enumerate(ordering)
        for film_id in slot
    }
    assert reported == derived, (reported, derived, boundaries)


async def assert_bands_well_formed(db: Database, account_id: uuid.UUID) -> None:
    """The dividers and anchors say what data-model.md says they say.

    Dividers appear in band order and inside the ordering; every pinned one names the
    band judgment that moved it, so no position exists that nobody can account for; and
    a band's anchor sits between that band's own dividers, since a canonical 4.0 living
    among the 3.5s is a contradiction in terms.
    """
    boundaries = await dividers(db, account_id)
    ordering = await ordering_snapshot(db, account_id)
    pinned = [boundaries[band] for band in BANDS[:-1] if band in boundaries]
    assert pinned == sorted(pinned), boundaries
    assert all(0 <= boundary <= len(ordering) for boundary in pinned), (boundaries, len(ordering))

    async with db.sessions() as session:
        unaudited = await session.scalar(
            text(
                """
                SELECT count(*) FROM dividers d
                LEFT JOIN comparison_log_entries e ON e.id = d.pinned_by_id
                WHERE d.account_id = :id AND (e.id IS NULL OR e.band IS NULL)
                """
            ),
            {"id": account_id},
        )
    assert unaudited == 0, "a divider moved to a position no judgment accounts for"

    seats = {film_id: index for index, slot in enumerate(ordering) for film_id in slot}
    for band, film_id in (await anchors(db, account_id)).items():
        assert film_id in seats, f"the {band} anchor is not a rated film"
        assert band_at(boundaries, seats[film_id]) == band, (
            f"the {band} anchor sits outside its own band"
        )


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


async def placement_trust(db: Database, account_id: uuid.UUID) -> dict[int, tuple[str, str]]:
    """Every placement as (trust, provenance), keyed by film."""
    async with db.sessions() as session:
        rows = await session.execute(
            text(
                """
                SELECT af.film_id, p.trust, p.provenance FROM placements p
                JOIN account_films af ON af.id = p.account_film_id
                WHERE p.account_id = :id
                """
            ),
            {"id": account_id},
        )
    return {film_id: (str(trust), str(provenance)) for film_id, trust, provenance in rows}


# --- Import-seeded tie-groups ---


async def seeded_films(db: Database, account_id: uuid.UUID) -> set[int]:
    """Films still sitting on an untouched import seed: provisional, and import-seeded."""
    async with db.sessions() as session:
        rows = await session.scalars(
            text(
                """
                SELECT af.film_id FROM placements p
                JOIN account_films af ON af.id = p.account_film_id
                WHERE p.account_id = :id
                  AND p.trust = 'provisional' AND p.provenance = 'import_seeded'
                """
            ),
            {"id": account_id},
        )
    return set(rows)


async def seeded_slots(db: Database, account_id: uuid.UUID) -> list[list[int]]:
    """The provisional tie-groups as they stand: the slots the import alone still holds."""
    seeded = await seeded_films(db, account_id)
    return [slot for slot in await ordering_snapshot(db, account_id) if set(slot) <= seeded]


def assert_seeded_slots_only_shrank(
    before: list[list[int]], after: list[list[int]], where: str = "the flow"
) -> None:
    """Import-seeded slots only ever shrink (data-model.md).

    A provisional tie-group is a placeholder, not a judgment, so comparisons dissolve it
    and nothing may ever add to it: a film joining one would be asserted equal to films
    it was never compared with. Every group left standing must therefore be part of one
    that was standing before.
    """
    groups = [set(slot) for slot in before]
    for slot in after:
        members = set(slot)
        assert any(members <= group for group in groups), (
            f"{where} grew an import-seeded tie-group: {sorted(members)} is in none of "
            f"{[sorted(group) for group in groups]}"
        )
