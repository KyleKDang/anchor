"""How well the owner remembers a film, as a sort key two flows share.

The spec names this ranking once, as the warmup's candidate ranking: the warmup offers
the best-remembered film per band as an anchor candidate (onboarding-and-import.md). It
lives here rather than inside the warmup because it is a fact about the owner's library
rather than about onboarding, and the criteria system's opponent choice asks the same
question of it.

Every term is a proxy for one question: which of these does the owner remember clearly
enough to judge? A film they went back to is remembered; a film they rated recently is
remembered; a film half the world has seen is at least recognisable. Profile favourites
jump the queue outright, because the owner has already named them as the ones that matter.

Popularity is read off the stored vote count rather than TMDB's own popularity figure,
which is a churning daily metric Anchor does not keep: a ranking that reshuffled
overnight for reasons inside TMDB would be worse than a stable one.
"""

import uuid
from collections.abc import Callable, Collection
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from anchor.models import (
    Film,
    ImportRow,
    ImportRowKind,
    ImportRowState,
    WatchEvent,
)

Key = Callable[[int], tuple[object, ...]]
"""A sort key over film ids, best-remembered first, total and stable."""


async def ranking(db: AsyncSession, account_id: uuid.UUID, film_ids: Collection[int]) -> Key:
    """The key ordering ``film_ids`` best-remembered first.

    Returned as a key rather than a sorted list so a caller can fold it into a wider
    order, and so the several reads behind it happen once for the whole set rather than
    once per film.
    """
    favorites = await _profile_favorites(db, account_id)
    rewatches = await _rewatch_counts(db, account_id)
    rated_at = await _rating_recency(db, account_id)
    popularity = await _vote_counts(db, film_ids)
    # A film with no rating date sorts just behind the oldest one that has a date, rather
    # than at the epoch: an unimported film is unknown, not ancient.
    epoch = min(rated_at.values(), default=None)
    unknown = epoch.timestamp() - 1 if epoch is not None else 0.0

    def key(film_id: int) -> tuple[object, ...]:
        when = rated_at.get(film_id)
        return (
            film_id not in favorites,
            -rewatches.get(film_id, 0),
            -(when.timestamp() if when is not None else unknown),
            -popularity.get(film_id, 0),
            film_id,
        )

    return key


async def _profile_favorites(db: AsyncSession, account_id: uuid.UUID) -> set[int]:
    """The films profile.csv named as favourites, as far as they bound to anything."""
    rows = await db.scalars(
        select(ImportRow.film_id).where(
            ImportRow.account_id == account_id,
            ImportRow.kind == ImportRowKind.profile_favorite,
            ImportRow.state.in_((ImportRowState.auto_matched, ImportRowState.bound)),
            ImportRow.film_id.is_not(None),
        )
    )
    return {film_id for film_id in rows if film_id is not None}


async def _rewatch_counts(db: AsyncSession, account_id: uuid.UUID) -> dict[int, int]:
    """How many times the owner went back to each film, imported diary rows included."""
    rows = await db.execute(
        select(WatchEvent.film_id, func.count())
        .where(WatchEvent.account_id == account_id, WatchEvent.rewatch.is_(True))
        .group_by(WatchEvent.film_id)
    )
    return {film_id: count for film_id, count in rows}


async def _rating_recency(db: AsyncSession, account_id: uuid.UUID) -> dict[int, datetime]:
    """When each imported rating was given, which is the freshness of the memory behind it."""
    rows = await db.execute(
        select(ImportRow.film_id, func.max(ImportRow.occurred_at))
        .where(
            ImportRow.account_id == account_id,
            ImportRow.kind == ImportRowKind.rating,
            ImportRow.film_id.is_not(None),
            ImportRow.occurred_at.is_not(None),
        )
        .group_by(ImportRow.film_id)
    )
    return {film_id: when for film_id, when in rows}


async def _vote_counts(db: AsyncSession, film_ids: Collection[int]) -> dict[int, int]:
    if not film_ids:
        return {}
    rows = await db.execute(
        select(Film.tmdb_id, Film.vote_count).where(Film.tmdb_id.in_(list(film_ids)))
    )
    return {tmdb_id: votes for tmdb_id, votes in rows}
