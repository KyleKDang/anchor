"""Settling: the owner asking a film to be placed again, and what is left to settle.

The fourth door into a re-placement (rating-system.md). The other three are the app
noticing something - a drift flag, a rewatch, a designation that does not match - and
this one is the owner simply saying so, from the film's page or from the "settling" mark
the film wears on Rated.

*The door is a mark, not a state read off the film.* A provisional film is provisional
whether or not the owner has asked about it, so "is this film mid-settle?" cannot be
answered by looking at the film - only by looking at what the owner did. That is why the
ask is stored, and why a reload of the placement screen on a film the owner bailed out of
shows where it landed rather than reopening the questions they walked away from.

*The head start is the whole point of the provisional door.* A trusted film asked to
re-place starts from its current slot with its old answers set aside, exactly as at a
rewatch, because the owner is questioning that position. A provisional film's position
was never a judgment in the first place, so nothing is set aside: every judgment the film
has collected - above all the ones other films' placements ran against it - counts as an
already-answered question, and a film others have narrowed is a question or two from
graduating.
"""

import uuid
from collections.abc import Collection
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from anchor import anchors as anchors_module
from anchor.models import (
    AccountFilm,
    Placement,
    PlacementTrust,
    ReplacementRequest,
)


async def request(db: AsyncSession, account_id: uuid.UUID, account_film: AccountFilm) -> None:
    """Record the owner asking for this film to be placed again, once per asking.

    A live request is reused rather than replaced. The mark carries the moment the flow
    began, and on a trusted film that moment is what sets the old answers aside - so
    re-stamping it mid-flow would set aside the answers the owner had just given and
    start them over, which is the opposite of the resume the screen promises.
    """
    if await replacing_since(db, account_id, account_film) is not None:
        return
    db.add(ReplacementRequest(account_id=account_id, account_film_id=account_film.id))


async def replacing_since(
    db: AsyncSession, account_id: uuid.UUID, account_film: AccountFilm
) -> datetime | None:
    """When the owner asked for the re-placement still running, or None where none is.

    Expiry rides on the placement's clock, as the rewatch door's does: landing restamps
    it, so a request older than the position it questioned has been answered by it. An
    early bail lands the film too, which is exactly what keeps a bailed-out settle from
    reopening on the next visit.
    """
    latest: datetime | None = await db.scalar(
        select(func.max(ReplacementRequest.requested_at)).where(
            ReplacementRequest.account_id == account_id,
            ReplacementRequest.account_film_id == account_film.id,
        )
    )
    if latest is None:
        return None
    placed_at = await db.scalar(
        select(Placement.placed_at).where(Placement.account_film_id == account_film.id)
    )
    if placed_at is not None and latest <= placed_at:
        return None
    return latest


async def provisional(db: AsyncSession, account_film: AccountFilm) -> bool:
    """Whether this film's position is the placeholder kind, which decides the door.

    Read live rather than stored on the request, so the answer is the film's own state at
    every step of the flow. It flips exactly once, when the film lands fully trusted, and
    by then the flow is over.
    """
    trust = await db.scalar(
        select(Placement.trust).where(Placement.account_film_id == account_film.id)
    )
    return trust is PlacementTrust.provisional


async def still_settling(
    db: AsyncSession, account_id: uuid.UUID, *, excluding: Collection[int] = ()
) -> set[int]:
    """The films still settling: provisional, never an anchor, minus ``excluding``.

    Anchors are never offered (onboarding-and-import.md): an anchor is re-placed from its
    own page, with the warning that landing outside its band retires it, so counting one
    here would offer a film the flow will not hand over. ``excluding`` is whatever the
    caller has already dealt with - the film on the done screen, or every film a sitting
    has been through - because the offer is always to settle *another* one.
    """
    anchored = set((await anchors_module.current(db, account_id)).values())
    rows = await db.scalars(
        select(AccountFilm.film_id)
        .select_from(Placement)
        .join(AccountFilm, AccountFilm.id == Placement.account_film_id)
        .where(
            Placement.account_id == account_id,
            Placement.trust == PlacementTrust.provisional,
        )
    )
    return set(rows) - anchored - set(excluding)


async def remaining(
    db: AsyncSession, account_id: uuid.UUID, *, excluding: Collection[int] = ()
) -> int:
    """How many films are still settling: the one count the design lets anywhere on screen.

    The strip atop Rated is its home and its ceiling - no dot, no chaser, no mention on
    any other screen (ADR 0011) - and the done screen of a settle the owner just finished
    is the single other place it appears, as the way onward from the film they just left.
    """
    return len(await still_settling(db, account_id, excluding=excluding))
