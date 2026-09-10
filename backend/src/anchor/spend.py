"""Month-to-date LLM spend: what the cap gate sums, and what the health check reports.

Its own module rather than a corner of :mod:`anchor.llm`, because its two readers live in
different processes. The seam sums the ledger before every dispatch and runs only in the
worker; the health check reports the same sums so a spent cap can be told from a broken
request without a shell on the box (#123), and runs only in the web process - which must
never import the seam (architecture.md). What they share is arithmetic over one table,
which touches no provider and so is safe for both to load.

Micros throughout, the ledger's own unit: a price of $X per million tokens is X millionths
of a dollar per token, so the configured price is already the per-token cost and a cap in
dollars becomes a ceiling in micros by one multiplication.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from anchor.models import SpendLedgerEntry


def micros(usd: float) -> int:
    return round(usd * 1_000_000)


def usd(amount_micros: int) -> float:
    """Back to dollars for a reader, exact to the micro since that is all a row can hold."""
    return round(amount_micros / 1_000_000, 6)


async def month_to_date(session: AsyncSession, *, account_id: uuid.UUID | None) -> int:
    """This calendar month's spend in micros: one account's, or the whole platform's.

    ``account_id=None`` is the global sum over every row, shared scope included - not the
    sum of the shared-scope rows. The two caps ask different questions of the same table,
    and only the account one narrows.
    """
    query = select(func.coalesce(func.sum(SpendLedgerEntry.cost_micros), 0)).where(_this_month())
    if account_id is not None:
        query = query.where(SpendLedgerEntry.account_id == account_id)
    return int(await session.scalar(query) or 0)


@dataclass(frozen=True)
class AccountsMonthToDate:
    """The per-account cap's month, seen from outside: how close the busiest account is,
    and how many are already over. Never which, because the reader may be anyone."""

    highest: int
    at_cap: int


async def across_accounts(session: AsyncSession, *, cap_micros: int) -> AccountsMonthToDate:
    """Every account's month at once, summarised to what an unauthenticated reader may see.

    Shared-scope rows are left out on purpose: they count against the platform cap and
    against nobody's, and folding them in here would report a capped account that does
    not exist.
    """
    per_account = (
        select(func.sum(SpendLedgerEntry.cost_micros).label("spent"))
        .where(_this_month(), SpendLedgerEntry.account_id.is_not(None))
        .group_by(SpendLedgerEntry.account_id)
        .subquery()
    )
    highest, at_cap = (
        await session.execute(
            select(
                func.coalesce(func.max(per_account.c.spent), 0),
                func.count().filter(per_account.c.spent >= cap_micros),
            )
        )
    ).one()
    return AccountsMonthToDate(highest=int(highest), at_cap=int(at_cap))


def _this_month() -> ColumnElement[bool]:
    return SpendLedgerEntry.created_at >= func.date_trunc("month", func.now())
