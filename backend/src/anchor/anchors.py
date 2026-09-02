"""The anchor lifecycle: designate, replace, retire, and the designation mismatch.

An anchor is the canonical exemplar of a band - "this film is what a 4.0 is" - at most
one per band, and it is the one place in Anchor where the owner assigns a band directly
(ADR 0002). Everything else about a rating is derived from position.

Designating is a band judgment, so it can pin dividers, and that is how a fresh account
gets its first band structure at all: an ordering with no dividers shows positions and
no stars until a designation erects the first ones (onboarding-and-import.md). The pin
lands as tight around the anchor as it can, because that is the least the judgment can
be read to claim - one film is a 4.0, not every film near it.

Two rules keep the owner in charge of the scale:

*Comparisons never move an anchor.* The designation is a mapping the owner writes and
only the owner clears; nothing in the placement flow or the advisory math touches it.

*An intent is never allowed to overrule a judgment.* Designating a film that is not
currently in the band does not move it there - it starts a re-placement seeded by the
intent, and the comparisons decide. Landing in the band completes the designation,
landing anywhere else cancels it, and either way the re-placement's result stands,
because real judgments are never discarded to protect an intent.
"""

import uuid
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from anchor import bands, jobs
from anchor import ordering as ordering_module
from anchor.accounts import CurrentAccount
from anchor.bands import Boundaries
from anchor.catalog import FilmCard
from anchor.deps import AppJobs, DbSession
from anchor.errors import ApiError
from anchor.models import (
    AccountFilm,
    AnchorDesignation,
    AnchorStatus,
    ComparisonContext,
    LifecycleState,
    Placement,
    PlacementTrust,
)
from anchor.ordering import Ordering

router = APIRouter(prefix="/api/anchors")


# --- Reading the designations ---


async def current(db: AsyncSession, account_id: uuid.UUID) -> dict[float, int]:
    """Band to film for every anchor the owner has designated, cheapest read there is."""
    rows = await db.execute(
        select(AnchorDesignation.band, AccountFilm.film_id)
        .join(AccountFilm, AccountFilm.id == AnchorDesignation.account_film_id)
        .where(
            AnchorDesignation.account_id == account_id,
            AnchorDesignation.status == AnchorStatus.current,
        )
    )
    return {band: film_id for band, film_id in rows}


async def intent(db: AsyncSession, account_id: uuid.UUID) -> AnchorDesignation | None:
    """The designation a re-placement is currently running for, if one is in flight."""
    held: AnchorDesignation | None = await db.scalar(
        select(AnchorDesignation).where(
            AnchorDesignation.account_id == account_id,
            AnchorDesignation.status == AnchorStatus.intended,
        )
    )
    return held


async def exemplars(
    db: AsyncSession,
    account_id: uuid.UUID,
    ordering: Ordering,
    boundaries: Boundaries,
    wanted: tuple[float, ...],
    exclude: int | None = None,
) -> dict[float, int]:
    """The film standing for each of ``wanted``: its anchor, or the best stand-in.

    A band with no anchor still has to be askable about, so the fallback is the most
    confidently-placed film nearest the band's middle - the closest thing the ordering
    has to an exemplar nobody designated. A band holding no films at all yields nothing,
    and the question that wanted it degrades to a plain band pick.

    ``ordering`` and ``boundaries`` must be the same sequence read two ways, or the
    bands come out of step. ``exclude`` drops the film the question is *about*, which
    is how a film is kept from standing in as the exemplar it is being compared to.
    """
    anchors = await current(db, account_id)
    trust = await _trust(db, account_id)
    derived = ordering_module.bands_of(ordering, boundaries)
    found = {}
    for band in wanted:
        if band in anchors and anchors[band] != exclude:
            found[band] = anchors[band]
            continue
        members = [
            (index, film_id)
            for index, slot in enumerate(ordering.slots)
            for film_id in slot.film_ids
            if derived.get(film_id) == band and film_id != exclude
        ]
        if not members:
            continue
        middle = (members[0][0] + members[-1][0]) / 2
        found[band] = min(
            members,
            key=lambda member: (
                trust.get(member[1]) is not PlacementTrust.full,
                abs(member[0] - middle),
                member[1],
            ),
        )[1]
    return found


async def _trust(db: AsyncSession, account_id: uuid.UUID) -> dict[int, PlacementTrust]:
    rows = await db.execute(
        select(AccountFilm.film_id, Placement.trust)
        .select_from(Placement)
        .join(AccountFilm, AccountFilm.id == Placement.account_film_id)
        .where(Placement.account_id == account_id)
    )
    return {film_id: trust for film_id, trust in rows}


# --- Wire shapes ---


class BandAnchor(BaseModel):
    """One band and the film the owner made canonical for it, where they have."""

    band: float
    film: FilmCard | None


class Anchors(BaseModel):
    anchors: list[BandAnchor]
    """All ten bands, best first, so a band with no anchor is still a place to designate."""
    nudge: bool
    """The anchor-designation nudge: presence-based, and gone at the first anchor."""


class Designated(BaseModel):
    """The designation took: the film was already where the owner said it was."""

    outcome: Literal["designated"] = "designated"
    band: float
    film: FilmCard
    retired: FilmCard | None
    """The anchor this one replaced, which stays exactly where it sits in the ordering."""


class ReplacementNeeded(BaseModel):
    """The film is not in that band, so comparisons - not the intent - get to decide."""

    outcome: Literal["re_placement"] = "re_placement"
    band: float
    film: FilmCard


Designation = Designated | ReplacementNeeded


class Designate(BaseModel):
    tmdb_id: int


# --- The flow ---


@router.get("")
async def read(account: CurrentAccount, db: DbSession) -> Anchors:
    anchors = await current(db, account.id)
    cards = await ordering_module.cards(db, list(anchors.values()))
    return Anchors(
        anchors=[
            BandAnchor(band=band, film=cards.get(anchors[band]) if band in anchors else None)
            for band in bands.BANDS
        ],
        nudge=not anchors,
    )


@router.post("/{band}")
async def designate(
    band: float, body: Designate, account: CurrentAccount, db: DbSession, queue: AppJobs
) -> Designation:
    """Make a rated film the canonical exemplar of a band, or start the flow that could.

    The app may suggest candidates but never designates on its own, so this only ever
    runs from the owner's own tap - a rated film's page, or a Rated band header.
    """
    _check_band(band)
    account_film = await _rated(db, account.id, body.tmdb_id)
    ordering = await ordering_module.load(db, account.id)
    index = ordering.index_of(body.tmdb_id)
    assert index is not None  # a rated film is a placed film
    boundaries = await bands.load(db, account.id)

    cards = await ordering_module.cards(db, [body.tmdb_id])
    if band not in bands.bands_possible_for_slot(boundaries, index):
        await _hold_intent(db, account.id, account_film, band)
        await db.commit()
        return ReplacementNeeded(band=band, film=cards[body.tmdb_id])

    retired = await _designate(db, account.id, account_film, band, index, boundaries)
    await jobs.schedule_retrain(db, queue, account.id)
    await db.commit()
    retired_cards = await ordering_module.cards(db, [retired] if retired else [])
    return Designated(
        band=band, film=cards[body.tmdb_id], retired=retired_cards.get(retired) if retired else None
    )


@router.delete("/{band}", status_code=204)
async def retire(band: float, account: CurrentAccount, db: DbSession, queue: AppJobs) -> None:
    """Retire a band's anchor. Changes no ratings and no dividers, by design.

    Dividers derive from judgments about ordinary films and go on holding the positions
    those judgments gave them, so the band survives losing its exemplar intact.
    """
    _check_band(band)
    await db.execute(
        delete(AnchorDesignation).where(
            AnchorDesignation.account_id == account.id,
            AnchorDesignation.band == band,
            AnchorDesignation.status == AnchorStatus.current,
        )
    )
    await jobs.schedule_retrain(db, queue, account.id)
    await db.commit()


async def _designate(
    db: AsyncSession,
    account_id: uuid.UUID,
    account_film: AccountFilm,
    band: float,
    index: int,
    boundaries: Boundaries,
) -> int | None:
    """Record the designation and pin what it forces; answers which anchor it replaced.

    The judgment goes in the log before the dividers move, because a divider naming a
    judgment that does not exist is a move nobody could audit.
    """
    judgment = bands.judgment(
        account_id,
        film_id=account_film.film_id,
        band=band,
        context=ComparisonContext.spontaneous,
    )
    db.add(judgment)
    await db.flush()
    await bands.move(db, account_id, boundaries, bands.pins_for(boundaries, index, band), judgment)

    replaced = await db.scalar(
        select(AccountFilm.film_id)
        .join(AnchorDesignation, AnchorDesignation.account_film_id == AccountFilm.id)
        .where(
            AnchorDesignation.account_id == account_id,
            AnchorDesignation.band == band,
            AnchorDesignation.status == AnchorStatus.current,
        )
    )
    # Designating replaces the band's old anchor and moves this film off whatever band
    # it anchored before; both are plain retirements, which change nothing else.
    await db.execute(
        delete(AnchorDesignation).where(
            AnchorDesignation.account_id == account_id,
            AnchorDesignation.status == AnchorStatus.current,
            (AnchorDesignation.band == band)
            | (AnchorDesignation.account_film_id == account_film.id),
        )
    )
    await db.execute(
        delete(AnchorDesignation).where(
            AnchorDesignation.account_id == account_id,
            AnchorDesignation.status == AnchorStatus.intended,
            AnchorDesignation.account_film_id == account_film.id,
        )
    )
    db.add(
        AnchorDesignation(
            account_id=account_id,
            band=band,
            account_film_id=account_film.id,
            status=AnchorStatus.current,
        )
    )
    await db.flush()
    return replaced


async def settle_intent(
    db: AsyncSession,
    account_id: uuid.UUID,
    account_film: AccountFilm,
    index: int,
    boundaries: Boundaries,
) -> bool:
    """Complete or cancel the designation a landing re-placement was running for.

    Landing in the band completes it; landing anywhere else cancels it, and the
    placement stands either way. Answers ``True`` where the designation completed.
    """
    held = await intent(db, account_id)
    if held is None or held.account_film_id != account_film.id:
        return False
    completed = held.band in bands.bands_possible_for_slot(boundaries, index)
    await db.execute(delete(AnchorDesignation).where(AnchorDesignation.id == held.id))
    if completed:
        await _designate(db, account_id, account_film, held.band, index, boundaries)
    return completed


async def retire_strays(
    db: AsyncSession, account_id: uuid.UUID, ordering: Ordering, boundaries: Boundaries
) -> None:
    """Retire every anchor a divider move has left outside its own band.

    A divider only ever moves as an owner judgment's direct consequence, and it carries
    a whole slot across when it does - so an anchor tied inside that slot changed band
    without being re-placed. The lifecycle's answer is the same either way: the status
    goes rather than the position, because a canonical 4.0 living among the 3.5s is a
    contradiction in terms, and the film keeps the slot its answers earned.
    """
    seats = {
        film_id: index for index, slot in enumerate(ordering.slots) for film_id in slot.film_ids
    }
    for band, film_id in (await current(db, account_id)).items():
        index = seats.get(film_id)
        if index is not None and band not in bands.bands_possible_for_slot(boundaries, index):
            await db.execute(
                delete(AnchorDesignation).where(
                    AnchorDesignation.account_id == account_id,
                    AnchorDesignation.band == band,
                    AnchorDesignation.status == AnchorStatus.current,
                )
            )


async def retire_if_outside(
    db: AsyncSession,
    account_id: uuid.UUID,
    account_film: AccountFilm,
    boundaries: Boundaries,
    index: int,
) -> bool:
    """Auto-retire an anchor a re-placement carried out of its own band.

    A canonical 4.0 living among the 3.5s is a contradiction in terms, so the status
    goes rather than the position: the film keeps the slot its answers earned.
    """
    designation = await db.scalar(
        select(AnchorDesignation).where(
            AnchorDesignation.account_id == account_id,
            AnchorDesignation.account_film_id == account_film.id,
            AnchorDesignation.status == AnchorStatus.current,
        )
    )
    if designation is None:
        return False
    if designation.band in bands.bands_possible_for_slot(boundaries, index):
        return False
    await db.execute(delete(AnchorDesignation).where(AnchorDesignation.id == designation.id))
    return True


async def _hold_intent(
    db: AsyncSession, account_id: uuid.UUID, account_film: AccountFilm, band: float
) -> None:
    """Park the owner's intent so the re-placement it starts can outlive this request."""
    await db.execute(
        delete(AnchorDesignation).where(
            AnchorDesignation.account_id == account_id,
            AnchorDesignation.status == AnchorStatus.intended,
        )
    )
    db.add(
        AnchorDesignation(
            account_id=account_id,
            band=band,
            account_film_id=account_film.id,
            status=AnchorStatus.intended,
        )
    )


def _check_band(band: float) -> None:
    if band not in bands.BANDS:
        raise ApiError(422, "not_a_band", "Ratings run from 0.5 to 5.0 in half-stars.")


async def _rated(db: AsyncSession, account_id: uuid.UUID, tmdb_id: int) -> AccountFilm:
    account_film = await db.scalar(
        select(AccountFilm).where(
            AccountFilm.account_id == account_id, AccountFilm.film_id == tmdb_id
        )
    )
    if account_film is None or account_film.state is not LifecycleState.rated:
        raise ApiError(409, "not_rated", "Rate this film before making it an anchor.")
    return account_film
