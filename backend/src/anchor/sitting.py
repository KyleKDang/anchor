"""A settling sitting: the placement flow run over provisional films one after another.

`settling.py` owns the per-film door - the owner asking one film to be placed again, from
its page or from its mark on the wall. This module owns the other door, the strip's: an
open-ended run that picks a film, hands it to the same flow, and picks another when the
owner is done with it (screens-and-flows.md, "Settling on screen").

*A sitting is not stored.* It has no target, no end, and nothing to resume: leaving is
free because every answer is already in the append-only log, and the next sitting simply
picks up whatever is still on the mark. What the sitting knows that the server does not is
which films it has already been through, so that is what the client sends back and the
only state the whole feature carries. "Not this one" is the same fact said out loud: it
moves on without a judgment, stores nothing, and the film is offered again the next time
the owner sits down, because the next-film rule already puts barely-remembered films last.

*Picking is the whole of the engine here.* The film offered is the one whose remaining
range is narrowest - the one closest to graduating, so a sitting feels like it is getting
somewhere - and ties break on how well the owner remembers the film, because a settled
film they remember cold becomes a confident pivot for every later placement and a
barely-remembered one produces skips (onboarding-and-import.md). Anchors are never offered.
"""

import uuid

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from anchor import ordering as ordering_module
from anchor import placement, remembered, settling
from anchor.accounts import CurrentAccount
from anchor.catalog import FilmCard
from anchor.deps import DbSession
from anchor.models import AccountFilm, ComparisonLogEntry
from anchor.ordering import Ordering

router = APIRouter(prefix="/api/settling")

OFFERED_LIMIT = 1000
"""A sitting cannot have been through more films than a library holds, several times over."""


class Sitting(BaseModel):
    """What the sitting has already been through, which is all it knows about itself."""

    offered: list[int] = Field(default_factory=list, max_length=OFFERED_LIMIT)
    """Films this sitting has already handed over: settled, bailed out of, or passed.

    Sent by the client rather than stored, because a sitting is a sitting and not a
    record. A film that graduated would drop out anyway; one the owner bailed on or
    passed would not, and offering it twice in a row is the one thing the run must not do.
    """


class NextFilm(BaseModel):
    """The next film to settle, and how much is left to settle after this one is picked."""

    film: FilmCard | None
    """None when nothing is left, which is how the sitting ends: it runs out."""
    remaining: int
    """Films still on the mark, this one included, minus whatever the sitting has passed.

    The header's "of about 7". About, because settling one film can graduate others it
    was compared against, so the number the owner sees is honest only at the moment it
    is read - which is exactly why it is written as an approximation.
    """


@router.post("/next")
async def next_film(body: Sitting, account: CurrentAccount, db: DbSession) -> NextFilm:
    """Pick the next film for this sitting, and open the settling door on it.

    Recording the ask here is what makes the stream one call per film: the flow itself is
    `POST /api/placements/{id}`, exactly as from the mark on the wall, and it needs the
    owner's ask on record before it will reopen a provisional film's questions. Asking
    twice is asking once, so a client that re-picks a film mid-sitting resumes it rather
    than throwing away the answers already given to it.
    """
    candidates = await settling.still_settling(db, account.id, excluding=body.offered)
    if not candidates:
        return NextFilm(film=None, remaining=0)

    chosen = await _narrowest(db, account.id, candidates)
    account_film: AccountFilm | None = await db.scalar(
        select(AccountFilm).where(
            AccountFilm.account_id == account.id, AccountFilm.film_id == chosen
        )
    )
    # A provisional placement always has its film rated, so the miss is unreachable in
    # practice; offering nothing is nonetheless better than a 500.
    if account_film is None:
        return NextFilm(film=None, remaining=len(candidates))
    await settling.request(db, account.id, account_film)
    await db.commit()

    cards = await ordering_module.cards(db, [chosen])
    return NextFilm(film=cards.get(chosen), remaining=len(candidates))


async def _narrowest(db: AsyncSession, account_id: uuid.UUID, candidates: set[int]) -> int:
    """The film with the narrowest remaining range, ties broken best-remembered first.

    Both halves are read fresh for the whole set at once rather than per film: the
    evidence in one query, the ranking's several in as many, so a library-sized sitting
    costs a constant number of round trips instead of one per film on the mark.
    """
    ordering = await ordering_module.load(db, account_id)
    collected = await placement.evidence(db, account_id, candidates)
    key = await remembered.ranking(db, account_id, candidates)

    def rank(film_id: int) -> tuple[object, ...]:
        return (_width(ordering, film_id, collected[film_id]), *key(film_id))

    return min(candidates, key=rank)


def _width(ordering: Ordering, film_id: int, collected: list[ComparisonLogEntry]) -> int:
    """How much of the ordering the film's landing is still loose across.

    The same head start the settling flow itself would get - every judgment the film has
    collected, as much of it as hangs together - run through the same search, so the
    number is literally how many slots of questions are left rather than a proxy for it.
    A film the owner has already tied to another is not loose at all: the tie ends the
    search wherever the bounds happen to stand.
    """
    reduced = ordering.without(film_id)
    core = placement.consistent_core(film_id, reduced, collected, [])
    search = placement.derive(film_id, reduced, core)
    if search.tied_with is not None:
        return 0
    return search.hi - search.lo
