"""Quality tags: what a film is known for, bought once for everybody.

A tag is a fact about the film rather than about anybody's taste - a thriller is known
for its tension whoever is looking at it - so the whole design follows from that one
sentence. Tags live in the shared catalog beside the film metadata, unscoped by account
(architecture.md); they are computed once per film ever and cached from then on; and the
ledger row for the call that bought them carries no account, because no single account
owns the answer.

*Once ever is a marker, not an absence.* A film the provider says is notable for nothing
has been tagged, and re-buying that answer every time somebody places it would be the
one way this feature could quietly cost real money - so the stamp lives on the film row
and the tag rows are what it stamped. The rolling TMDB re-sync deliberately leaves the
stamp alone: fresh metadata is not a reason to re-ask a question about the film itself.

*Nothing waits on them.* Tagging is precompute like every other LLM job
(taste-profile.md): the placement that asks for a film's tags lands without them, and
they are there for the next one. Their only consumer in v1 is criteria pair selection,
which prefers a pair sharing a tag and rotates through the quality list when no pair
does - so an untagged film, or a month whose cap is spent, costs the owner a slightly
less pointed bonus question and nothing else.

*Spend is still earned.* The seam's readiness gate is account-scoped, and shared work
passes it by construction, so the gate for tags is here instead - at the one place that
asks for them. An account that has not reached *forming* never causes a tag to be
bought, which is what keeps "hollow flood accounts cost nothing" (taste-profile.md) true
of the whole bill rather than of its account-scoped part.
"""

import logging
import uuid
from collections.abc import Collection, Iterable
from datetime import UTC, datetime

import procrastinate
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from anchor import readiness
from anchor.models import BUILT_IN_QUALITIES, Film, QualityTag
from anchor.settings import Settings

log = logging.getLogger(__name__)


async def by_film(db: AsyncSession, film_ids: Collection[int]) -> dict[int, frozenset[str]]:
    """Each film's tags, keyed by film id; a film with no tags gets an empty set.

    Untagged and tagged-with-nothing read alike here, on purpose: both answer the only
    question the consumer asks - what this film shares with another - with "nothing". The
    difference between them matters to :func:`_untagged`, which is about spending money.
    """
    if not film_ids:
        return {}
    found: dict[int, set[str]] = {film_id: set() for film_id in film_ids}
    rows = await db.execute(
        select(QualityTag.film_id, QualityTag.quality).where(QualityTag.film_id.in_(film_ids))
    )
    for film_id, quality in rows:
        found[film_id].add(quality)
    return {film_id: frozenset(qualities) for film_id, qualities in found.items()}


async def schedule(
    db: AsyncSession,
    queue: procrastinate.App,
    account_id: uuid.UUID,
    film_ids: Iterable[int],
    settings: Settings,
) -> None:
    """Queue the tagging of any of these films nobody has paid for yet.

    Called from a placement's landing with the films a card from that landing could name,
    which is the set the next one will look tags up for. Bounded by the flow rather than
    by the library, on purpose: tagging everything a six-hundred-row import brought in
    would buy hundreds of answers to a question nobody is going to ask.

    The owner's criteria frequency is deliberately not consulted. Their *tags* are not
    their setting: the setting governs how often a card is offered after a placement, and
    a tag stays useful to the film page's question session, which is available whatever
    the frequency says (taste-profile.md) - and useful to every other account, since the
    catalog is shared. An owner who has turned the cards off has not asked for a library
    nobody may ever tag.

    Enqueued in the caller's transaction, so a landing that rolls back takes its tagging
    with it. Two placements racing on the same film both queue a job; the per-film lock
    runs them in turn, and the second finds the work already done.
    """
    from anchor import jobs

    wanted = await _untagged(db, list(film_ids))
    # The cheap question first, and the readiness counts only if it says yes: a landing on
    # a warm catalog is the common case, it finds nothing to buy, and it should not pay
    # for an answer about spend it is not about to make.
    if not wanted:
        return
    if not await readiness.earned_spend(db, account_id, settings):
        return
    for tmdb_id in wanted:
        await jobs.enqueue(db, queue, jobs.tag_film, lock=lock_for(tmdb_id), tmdb_id=tmdb_id)


async def _untagged(db: AsyncSession, film_ids: Collection[int]) -> list[int]:
    """Which of these films nobody has asked the provider about yet."""
    if not film_ids:
        return []
    rows = await db.scalars(
        select(Film.tmdb_id).where(Film.tmdb_id.in_(film_ids), Film.tagged_at.is_(None))
    )
    return list(rows)


def lock_for(tmdb_id: int) -> str:
    """One queue lock per film, so two accounts placing it at once buy its tags once."""
    return f"tags:{tmdb_id}"


async def pending(db: AsyncSession, tmdb_id: int) -> Film | None:
    """The film if it still wants tagging, or None if it does not - the job's guard.

    Detached before it is handed back, because the caller holds it across a provider
    call that must not sit inside an open transaction: what the prompt reads is the row
    as it was, and nothing about the film is expected to change while it is asked about.
    """
    film = await db.get(Film, tmdb_id)
    if film is None or film.tagged_at is not None:
        return None
    db.expunge(film)
    return film


async def record(db: AsyncSession, tmdb_id: int, named: Iterable[str]) -> None:
    """Stamp the film tagged and write the tags the answer named, or stamp it alone.

    Filtered to the built-in vocabulary here rather than trusted from the caller, so
    "tags draw from the built-in vocabulary only" (data-model.md) is a property of the
    table rather than of whoever last wrote to it. The seam filters a provider's answer
    too; this is the one that holds for every writer, and it reads the names back in the
    vocabulary's own order, which also drops duplicates.

    ``ON CONFLICT DO NOTHING`` rather than a read first: the primary key is the data
    model's own (film, vocabulary quality), so a duplicate cannot be stored and there is
    nothing left to decide when a re-run produces one.
    """
    film = await db.get(Film, tmdb_id)
    if film is None or film.tagged_at is not None:
        return
    wanted = {quality.strip().casefold() for quality in named}
    rows = [
        {"film_id": tmdb_id, "quality": quality}
        for quality in BUILT_IN_QUALITIES
        if quality.casefold() in wanted
    ]
    if rows:
        await db.execute(insert(QualityTag).values(rows).on_conflict_do_nothing())
    film.tagged_at = datetime.now(UTC)
