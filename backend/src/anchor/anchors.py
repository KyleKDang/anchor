"""Anchors: the films the owner is certain of, marked and retired from the film page.

An anchor is a film the owner has marked as one they are sure about - a definitive 5.0,
a definitive 3.5 (ADR 0013). Any number per band, and a band's marked films are its
anchor pool: what the band picker shows for that band, and what its comparisons draw on.

The whole lifecycle is one toggle. Marking sets the placement's ``anchored_at``,
retiring clears it, and neither changes anything else - not the rating, not the rank,
not another film. There is no designation entity and no designation history, because an
anchor is a property of where a film already sits rather than a mapping the owner writes
over the top of the ordering.

*An anchor sits wherever it sits in its band.* It is a bound, never a floor or a
ceiling: the pool of the 5.0 band is naturally the owner's very best films, and the pool
of the 3.0 band its typical members, so losing to every 5.0 anchor makes a film at most
a 5.0 rather than making it a 4.5.

*A move across bands retires.* A reference that moved is no longer certain, so any write
that carries a film into another band clears the mark on its way past - which is what
makes "an anchor is always in the band it was marked in" true by construction.
"""

import uuid

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from anchor import jobs
from anchor import ordering as ordering_module
from anchor.accounts import CurrentAccount
from anchor.catalog import FilmCard
from anchor.deps import AppJobs, DbSession
from anchor.errors import ApiError
from anchor.models import BANDS, AccountFilm, LifecycleState, Placement

router = APIRouter(prefix="/api/anchors")


# --- Reading the pools ---


async def pools(db: AsyncSession, account_id: uuid.UUID) -> dict[float, list[int]]:
    """Each band's anchor pool, most recently marked first.

    That order is the one the exemplar cap reads (taste-profile.md): where a pool has
    grown past what a prompt can carry, the films the owner marked most recently are
    the ones that stand for the band.
    """
    rows = await db.execute(
        select(Placement.band, AccountFilm.film_id)
        .join(AccountFilm, AccountFilm.id == Placement.account_film_id)
        .where(Placement.account_id == account_id, Placement.anchored_at.is_not(None))
        .order_by(Placement.band.desc(), Placement.anchored_at.desc(), AccountFilm.film_id)
    )
    found: dict[float, list[int]] = {}
    for band, film_id in rows:
        found.setdefault(band, []).append(film_id)
    return found


async def marked(db: AsyncSession, account_id: uuid.UUID) -> set[int]:
    """Every film carrying an anchor mark: what the wall badges."""
    rows = await db.scalars(
        select(AccountFilm.film_id)
        .select_from(Placement)
        .join(AccountFilm, AccountFilm.id == Placement.account_film_id)
        .where(Placement.account_id == account_id, Placement.anchored_at.is_not(None))
    )
    return set(rows)


async def counts(db: AsyncSession, account_id: uuid.UUID) -> dict[float, int]:
    """How many anchors each band holds: the count on every band header."""
    rows = await db.execute(
        select(Placement.band, func.count())
        .where(Placement.account_id == account_id, Placement.anchored_at.is_not(None))
        .group_by(Placement.band)
    )
    return {band: count for band, count in rows}


# --- Wire shapes ---


class BandPool(BaseModel):
    """One band and the films the owner has marked in it, most recent first."""

    band: float
    films: list[FilmCard]


class Anchors(BaseModel):
    """Every band's pool, best band first. A band with no anchors is still listed."""

    bands: list[BandPool]


# --- The toggle ---


@router.get("")
async def read(account: CurrentAccount, db: DbSession) -> Anchors:
    found = await pools(db, account.id)
    cards = await ordering_module.cards(
        db, [film_id for pool in found.values() for film_id in pool]
    )
    return Anchors(
        bands=[
            BandPool(
                band=band,
                films=[cards[film_id] for film_id in found.get(band, ()) if film_id in cards],
            )
            for band in BANDS
        ]
    )


@router.post("/{tmdb_id}", status_code=204)
async def mark(tmdb_id: int, account: CurrentAccount, db: DbSession, queue: AppJobs) -> None:
    """Mark a rated film an anchor. Marking an already-marked film changes nothing.

    Re-stamping the moment would quietly reshuffle the exemplar set on a tap that said
    nothing new, so the mark the owner already made is the one that stands.
    """
    placement = await _anchorable(db, account.id, tmdb_id)
    if placement.anchored_at is None:
        placement.anchored_at = func.now()
        await db.flush()
        await jobs.schedule_retrain(db, queue, account.id)


@router.delete("/{tmdb_id}", status_code=204)
async def retire(tmdb_id: int, account: CurrentAccount, db: DbSession, queue: AppJobs) -> None:
    """Retire the mark. Changes nothing else: same band, same rank, same rating."""
    placement = await _anchorable(db, account.id, tmdb_id)
    if placement.anchored_at is not None:
        placement.anchored_at = None
        await db.flush()
        await jobs.schedule_retrain(db, queue, account.id)


async def _anchorable(db: AsyncSession, account_id: uuid.UUID, tmdb_id: int) -> Placement:
    """The placement of a rated film. Only a rated film has a band to be an anchor of."""
    placement = await db.scalar(
        select(Placement)
        .join(AccountFilm, AccountFilm.id == Placement.account_film_id)
        .where(
            Placement.account_id == account_id,
            AccountFilm.film_id == tmdb_id,
            AccountFilm.state == LifecycleState.rated,
        )
    )
    if placement is None:
        raise ApiError(404, "not_rated", "Only a rated film can be an anchor.")
    return placement
