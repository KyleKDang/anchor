"""The demo account's read-only enforcement, in the one place each half of it belongs.

Two halves, and they guard different things.

The *request* half hangs off :func:`anchor.accounts.current_account`, the single door
every authenticated endpoint depends on. A write method arriving through that door on a
flagged account is refused there, before the handler runs, so a write path added later is
guarded by having been written at all rather than by remembering to say so. There is one
error code for it, ``demo_read_only``, because the frontend keys its signup pitch on
exactly one thing.

The *engine* half is the same rule pointed inwards. Nothing the owner does can reach a
flagged account, but Anchor's own maintenance is not something the owner does: restocks,
shelf rotation, retrains and tier maintenance all run off a visit, and a demo account is
visited constantly. So the maintenance asks this module first, and a flagged account
stays exactly as it was built however many visitors walk through it (demo-account.md).

Nothing here creates a demo account or reaches one: the flag is set by the fixture build,
which lands separately.
"""

import functools
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from anchor.errors import ApiError
from anchor.models import Account

CODE = "demo_read_only"
"""The one refusal the frontend keys on. Every mutating route answers with this or none."""

MESSAGE = "This is a read-only demo account. Sign up to build your own."

WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
"""HTTP's own account of what a mutation is, which is the line the guard reads."""


def read_only() -> ApiError:
    return ApiError(403, CODE, MESSAGE)


def guard(request: Request, account: Account) -> None:
    """Refuse a write arriving on a demo session, from inside the app's one auth door.

    Reading the method rather than a per-route marker is what makes this structural. A
    route added tomorrow that mutates does so under one of the write verbs, and is
    refused without its author having heard of this module; a route that opts out of the
    guard has to opt out of ``current_account``, which is a conspicuous thing to write.

    The auth endpoints are the deliberate hole and need no exemption here, because none of
    them depends on this door: signup and login already refuse a flagged account by other
    means (it has no password and its address reads as taken), and logout reads the cookie
    directly so a visitor can always leave.
    """
    if account.is_demo and request.method in WRITE_METHODS:
        raise read_only()


async def flagged(db: AsyncSession, account_id: uuid.UUID) -> bool:
    """Whether this account is the demo, asked by the engine before it maintains anything.

    A primary-key read on a row every one of these call sites has already loaded the id
    of, and only on the paths that would otherwise write - so an ordinary account pays one
    indexed lookup per visit-gated read, and the demo pays it instead of a restock.
    """
    return bool(await db.scalar(select(Account.is_demo).where(Account.id == account_id)))


def skips_demo(
    task: Callable[..., Awaitable[None]],
) -> Callable[..., Awaitable[None]]:
    """Wrap an account-scoped job so a flagged account's copy of it does nothing.

    The enqueue side is already closed - every flow that schedules one of these runs
    behind the request guard, or behind a visit gate that asks :func:`flagged` itself -
    so this is the backstop rather than the gate, and it is the layer the tests drive
    because it holds whatever queued the job.

    Applied where the tasks are declared rather than as a decorator on each definition, so
    the registration is the single list of what is account-scoped and a task that grows an
    ``account_id`` cannot be registered without it.
    """

    @functools.wraps(task)
    async def skipping(context: Any, account_id: str) -> None:
        # Imported here rather than at the top of the module because jobs.py imports this
        # one to apply the wrap, and the two would form a cycle at import time.
        from anchor import jobs

        async with jobs.database_of(context).sessions() as session:
            if await flagged(session, uuid.UUID(account_id)):
                return
        await task(context, account_id)

    # Named rather than inferred from ``__wrapped__``, which any decorator would set: the
    # suite asks the queue's own registry whether each account-scoped task carries this
    # one, and a task wrapped in something else must not answer yes.
    skipping.skips_demo = True  # type: ignore[attr-defined]
    return skipping
