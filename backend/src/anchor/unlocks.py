"""The two one-time dots: Discovery at *forming*, Watchlist at *ready*.

The dot is the only nav-level marker in the product and nothing else ever gets one
(ADR 0011). It fires once per unlock per account, which is exactly the fact readiness
cannot supply: readiness is a pure function of the evidence, so it would light the dot
again on every read forever. What is stored is therefore not the state but the *event* -
that this account crossed this bar, and whether the owner has been to the screen since.

Arming is idempotent and every surface that could be the first to notice calls it: the
nav's own read, the screen itself, a landing, the end of an import. Only the first of
them gets ``True`` back, which is what lets the act that crossed the bar say so on its
own done screen while the nav quietly shows a dot for everybody else.

A seed import of any real size clears both bars in one go (onboarding-and-import.md), so
arming reads the whole ladder rather than one rung: an account that has never been below
*ready* still earns the Discovery dot, because it did unlock discovery - it just did it
in the same instant.
"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from anchor.models import Unlock, UnlockMark
from anchor.readiness import Readiness
from anchor.readiness import state as readiness_state
from anchor.settings import Settings

EARNED_BY: dict[Unlock, Readiness] = {
    Unlock.discovery: Readiness.forming,
    Unlock.watchlist: Readiness.ready,
}
"""Which readiness state lights which dot (onboarding-and-import.md, "Feature light-up")."""

LADDER: tuple[Readiness, ...] = (Readiness.cold, Readiness.forming, Readiness.ready)
"""The states in order, so "at least forming" is a comparison rather than a special case."""


async def arm(db: AsyncSession, account_id: uuid.UUID, settings: Settings) -> set[Unlock]:
    """Light the dots this account has now earned, and say which ones were newly lit.

    Returns the unlocks that crossed *in this call*, so a caller can name them on the
    screen of the act that earned them and every later caller says nothing.
    """
    state = await readiness_state(db, account_id, settings)
    already = {mark.unlock for mark in await _marks(db, account_id)}
    crossed = {
        unlock
        for unlock, needed in EARNED_BY.items()
        if unlock not in already and LADDER.index(state) >= LADDER.index(needed)
    }
    for unlock in crossed:
        db.add(UnlockMark(account_id=account_id, unlock=unlock))
    if crossed:
        await db.flush()
    return crossed


async def pending(db: AsyncSession, account_id: uuid.UUID) -> set[Unlock]:
    """The dots the nav should currently be showing."""
    return {mark.unlock for mark in await _marks(db, account_id) if mark.seen_at is None}


async def clear(db: AsyncSession, account_id: uuid.UUID, unlock: Unlock) -> None:
    """First visit to the unlocked screen: the dot has done its job and never returns."""
    for mark in await _marks(db, account_id):
        if mark.unlock is unlock and mark.seen_at is None:
            mark.seen_at = func.now()
            await db.flush()


async def _marks(db: AsyncSession, account_id: uuid.UUID) -> list[UnlockMark]:
    rows = await db.scalars(select(UnlockMark).where(UnlockMark.account_id == account_id))
    return list(rows)
