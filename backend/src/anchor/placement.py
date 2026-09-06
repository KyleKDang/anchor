"""The band picker: the whole of rating a film.

The picker shows the ten bands, each with its anchor pool, and the owner picks one. That
is the rating - no questions asked, because with the pools on screen the pick has already
been made against the owner's own references, and a check after every rating would be the
chore this design removed (ADR 0013).

The film lands in its band at the rank the default order gives it, and the done screen
says where that is. Correcting it is the wall's job, not a step here: the primary way on
from the done screen is "adjust on the wall", and there is no undo button because the
wall *is* the undo.

*The picker holds no state.* A rating in progress needs no entity (data-model.md): the
band the owner taps is the whole of the input, so opening the picker is a read and
abandoning it is walking away. The film was already watched-unrated and already seated in
the rate-later queue before the picker opened, which is what makes walking away safe
without the client having to signal that it happened.

*Re-rating is the same picker.* Opened from a film's own page or from a rewatch, with the
current band marked. Landing in the same band keeps the rank, landing in another takes
the default rank there and retires the anchor mark (rating-system.md).

Ranges, comparisons, the boundary question and the last-resort pick are the picker
ticket that follows this one; what stands here is the single pick, which is the path
every range ends on anyway.
"""

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from anchor import anchors as anchors_module
from anchor import criteria, jobs, tags
from anchor import ordering as ordering_module
from anchor import unlocks as unlocks_module
from anchor.accounts import CurrentAccount
from anchor.catalog import FilmCard
from anchor.criteria import CriteriaCard
from anchor.deps import AppJobs, AppSettings, DbSession
from anchor.errors import ApiError
from anchor.models import (
    BANDS,
    Account,
    AccountFilm,
    ComparisonContext,
    ComparisonKind,
    ComparisonLogEntry,
    Film,
    LifecycleState,
    Unlock,
)

router = APIRouter(prefix="/api/placements")

POOL_SHOWN = 5
"""Anchors shown per band row. A handful of posters, with a count where the pool is
larger (screens-and-flows.md): the row is a reminder of the owner's references, not a
list of them."""


# --- Wire shapes ---


class BandRow(BaseModel):
    """One band of the picker: its value, and the references the owner picks against."""

    band: float
    pool: list[FilmCard]
    """Up to :data:`POOL_SHOWN` of the band's anchors, most recently marked first."""
    pool_total: int
    """The whole pool's size, so a row can say how many more it stands for."""


class Picker(BaseModel):
    """The picker screen: the film being rated, and the ten bands to put it in."""

    film: FilmCard
    bands: list[BandRow]
    current_band: float | None
    """The film's band on a re-rate, marked on the row; None when it is not rated yet."""
    current_rank: int | None
    """Where it currently sits in that band, shown beside the mark on a re-rate."""


class Neighbours(BaseModel):
    """The films immediately above and below a landed film, inside its own band.

    Band-local because the rank is: the done screen says "third of your 4.0s", so the
    films that statement is against are the other 4.0s. An end of the row has no
    neighbour that way, and None is the honest answer.
    """

    above: FilmCard | None
    below: FilmCard | None


class Landed(BaseModel):
    """The done screen: where the film landed, and the two ways on from it."""

    film: FilmCard
    band: float
    rank: int
    band_size: int
    """How many films the band holds, so the rank reads as "3 of 41"."""
    anchor: bool
    """The film carries an anchor mark, which a cross-band re-rate has just retired."""
    neighbours: Neighbours
    unlocked: list[Unlock]
    """What this very landing unlocked, and empty on every other one.

    One line, once ever, on the screen of the act that earned it (surfacing.md); the only
    other marker an unlock gets is the nav's one-time dot.
    """
    anchor_nudge: bool
    """The account has no anchors at all: the one line saying what marking one does."""
    criteria: CriteriaCard | None = None
    """The optional bonus question this landing earned, and usually None (taste-profile.md).

    It rides on the done screen rather than arriving from a call of its own because it is
    a bonus: a screen that had to fetch it could be left waiting on something the owner
    never asked for. Answering it is optional and ignoring it costs nothing.
    """


class Pick(BaseModel):
    """The owner's answer: one of the ten bands, and nothing else to decide."""

    band: float


# --- The flow ---


@router.get("/{tmdb_id}")
async def picker(tmdb_id: int, account: CurrentAccount, db: DbSession) -> Picker:
    """Open the band picker on a watched film, or on a rated one to re-rate it.

    A pure read: the picker keeps no state, so this is safe to call again at any time and
    the screen does exactly that on every mount.
    """
    await _rateable(db, account, tmdb_id)
    film = await _film(db, tmdb_id)
    pools = await anchors_module.pools(db, account.id)
    cards = await ordering_module.cards(
        db, [film_id for pool in pools.values() for film_id in pool[:POOL_SHOWN]]
    )
    placement = await ordering_module.placement_of(db, account.id, tmdb_id)
    return Picker(
        film=FilmCard.of(film),
        bands=[
            BandRow(
                band=band,
                pool=[
                    cards[film_id]
                    for film_id in pools.get(band, ())[:POOL_SHOWN]
                    if film_id in cards
                ],
                pool_total=len(pools.get(band, ())),
            )
            for band in BANDS
        ],
        current_band=placement.band if placement else None,
        current_rank=placement.rank if placement else None,
    )


@router.post("/{tmdb_id}/band")
async def pick(
    tmdb_id: int,
    body: Pick,
    account: CurrentAccount,
    db: DbSession,
    queue: AppJobs,
    settings: AppSettings,
) -> Landed:
    """Rate the film: the band the owner tapped, at the rank the default order gives it.

    This is the one write in the whole flow. It appends the band pick to the log, seats
    the film, arms whatever readiness bar it just crossed, and hands back the done screen.
    """
    if body.band not in BANDS:
        raise ApiError(422, "not_a_band", "A rating is one of the ten half-star values.")
    account_film = await _rateable(db, account, tmdb_id)
    film = await _film(db, tmdb_id)
    order = await ordering_module.default_order(db, settings.default_order_prior_votes)
    placement = await ordering_module.placement_of(db, account.id, tmdb_id)
    context = ComparisonContext.re_placement if placement else ComparisonContext.placement

    db.add(
        ComparisonLogEntry(
            account_id=account.id,
            kind=ComparisonKind.band_pick,
            subject_film_id=tmdb_id,
            film_a_id=tmdb_id,
            film_b_id=None,
            verdict=None,
            band=body.band,
            context=context,
        )
    )
    if placement is None:
        rank = await ordering_module.default_rank(db, account.id, body.band, film, order)
        await ordering_module.land(db, account_film, band=body.band, rank=rank)
    else:
        rank = (
            placement.rank
            if body.band == placement.band
            else await ordering_module.default_rank(db, account.id, body.band, film, order)
        )
        await ordering_module.re_rate(db, placement, band=body.band, rank=rank)

    await jobs.schedule_retrain(db, queue, account.id)
    crossed = await unlocks_module.arm(db, account.id, settings)
    card = await criteria.offer(db, account, tmdb_id, context)
    # The films a card from this rating could have named are the ones the next card will
    # want tags for, so this rating is what buys them - for the next one, never for this
    # one. Precompute only: nothing above waits on the job this queues.
    await tags.schedule(
        db, queue, account.id, await criteria.askable_films(db, account.id, tmdb_id), settings
    )
    landed = await _landed(db, account, tmdb_id, unlocked=crossed, card=card)
    await db.commit()
    return landed


async def _landed(
    db: AsyncSession,
    account: Account,
    tmdb_id: int,
    *,
    unlocked: set[Unlock],
    card: CriteriaCard | None,
) -> Landed:
    """The done screen, read back off the ordering the pick just wrote."""
    ordering = await ordering_module.load(db, account.id)
    placed = ordering.of(tmdb_id)
    assert placed is not None  # the film was rated inside this request
    above, below = ordering.neighbours(tmdb_id)
    cards = await ordering_module.cards(
        db, [tmdb_id, *(film_id for film_id in (above, below) if film_id is not None)]
    )
    return Landed(
        film=cards[tmdb_id],
        band=placed.band,
        rank=placed.rank,
        band_size=len(ordering.row(placed.band)),
        anchor=placed.anchored,
        neighbours=Neighbours(
            above=cards.get(above) if above else None,
            below=cards.get(below) if below else None,
        ),
        unlocked=[unlock for unlock in Unlock if unlock in unlocked],
        anchor_nudge=not any(ordering.anchors(band) for band in ordering.bands()),
        criteria=card,
    )


# --- Helpers ---


async def _rateable(db: AsyncSession, account: Account, tmdb_id: int) -> AccountFilm:
    """The film's record, refusing anything the owner has not said they watched."""
    account_film: AccountFilm | None = await db.scalar(
        select(AccountFilm).where(
            AccountFilm.account_id == account.id, AccountFilm.film_id == tmdb_id
        )
    )
    if account_film is None or account_film.state is LifecycleState.backlog:
        raise ApiError(409, "not_watched", "Mark this film watched before rating it.")
    return account_film


async def _film(db: AsyncSession, tmdb_id: int) -> Film:
    film = await db.get(Film, tmdb_id)
    if film is None:
        raise ApiError(404, "unknown_film", "That film is not in the catalog.")
    return film
