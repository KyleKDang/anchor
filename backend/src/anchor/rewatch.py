"""Rewatches: the watch is logged, and one light question is offered on the way past.

Offer, never force (rating-system.md). Marking a film watched again timestamps the watch
and asks whether it still feels the same; confirming keeps everything as it is and is
recorded as such, changing your mind opens the band picker with the current band marked,
and walking away is a first-class answer that nothing ever chases.

The watch event is appended whatever happens next, because it is history and the question
is optional.
"""

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from anchor import tier
from anchor.accounts import CurrentAccount
from anchor.deps import DbSession
from anchor.errors import ApiError
from anchor.models import (
    AccountFilm,
    RewatchOutcome,
    WatchEvent,
    WatchOrigin,
)

router = APIRouter(prefix="/api/rewatches")


class RewatchPrompt(BaseModel):
    """The still-feel-the-same offer, open on the film page until it is answered."""

    watched_at: datetime
    """The rewatch this question belongs to; the offer belongs to that moment."""


class Answer(BaseModel):
    """The three answers, in the owner's terms rather than the log's.

    ``changed`` is the only one that leads anywhere: the client opens the band picker on
    it, and the owner's pick decides the rating the same way every other pick does.
    Confirming and skipping both end here, which is the point of offering rather than
    nagging.
    """

    answer: Literal["confirmed", "changed", "skip"]


OUTCOMES: dict[str, RewatchOutcome] = {
    "confirmed": RewatchOutcome.confirmed,
    "changed": RewatchOutcome.re_rated,
    "skip": RewatchOutcome.skipped,
}


async def log(db: AsyncSession, account_id: uuid.UUID, account_film: AccountFilm) -> WatchEvent:
    """Append the rewatch.

    The watch goes in whether or not the question is ever answered: watched-ness is not
    conditional on having an opinion about it, and the watch clock every cooldown is
    denominated in counts this event the moment it exists.
    """
    event = WatchEvent(
        account_id=account_id,
        film_id=account_film.film_id,
        # Stamped the way any other watch is, rather than assuming a rated film holds no
        # tier seat: where the film stood is the tier's answer to give, not this module's.
        standing=tier.standing(account_film),
        origin=WatchOrigin.hand_added,
        rewatch=True,
    )
    db.add(event)
    return event


@router.post("/{tmdb_id}", status_code=204)
async def answer(tmdb_id: int, body: Answer, account: CurrentAccount, db: DbSession) -> None:
    """Answer the still-feel-the-same question the last rewatch left open.

    "Changed" records nothing about where the film should go - only that the owner wants
    to look again. The picker's own answer decides, opened on the band the film holds
    today, because that is the rating being questioned.
    """
    event = await pending(db, account.id, tmdb_id)
    if event is None:
        raise ApiError(409, "no_rewatch", "That film has no rewatch waiting to be answered.")
    event.rewatch_outcome = OUTCOMES[body.answer]
    await db.commit()


async def pending(db: AsyncSession, account_id: uuid.UUID, film_id: int) -> WatchEvent | None:
    """The rewatch still waiting on an answer, or None where nothing is open.

    Only the latest one is ever open: an unanswered question from three rewatches ago is
    a moment long past, and re-asking it would be about a viewing the owner has stopped
    thinking about.
    """
    event: WatchEvent | None = await db.scalar(
        select(WatchEvent)
        .where(
            WatchEvent.account_id == account_id,
            WatchEvent.film_id == film_id,
            WatchEvent.rewatch.is_(True),
        )
        .order_by(WatchEvent.watched_at.desc(), WatchEvent.id.desc())
        .limit(1)
    )
    if event is None or event.rewatch_outcome is not None:
        return None
    return event


async def prompt(db: AsyncSession, account_id: uuid.UUID, film_id: int) -> RewatchPrompt | None:
    event = await pending(db, account_id, film_id)
    return None if event is None else RewatchPrompt(watched_at=event.watched_at)
