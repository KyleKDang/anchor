"""The account's quality list: the built-in dozen, seeded, and what rotates through it.

One canonical list per account sits behind both the quality picker (#35) and criteria
questions. It is seeded with the built-in vocabulary the moment the account becomes
real, so nothing downstream ever has to cope with an account that has no qualities yet,
and owner additions arriving later are ordinary entries on the same list - askable, and
treated identically everywhere.

The system never invents entries. Everything on the list is either the closed built-in
vocabulary or something the owner typed into the picker's free text.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from anchor.models import BUILT_IN_QUALITIES, QualityListEntry, QualityOrigin


async def seed(db: AsyncSession, account_id: uuid.UUID) -> None:
    """Give a brand-new account the built-in dozen, in the vocabulary's own order.

    Called once, when the account is verified: before that the account is inert and the
    account record is the only row it may have (data-model.md).
    """
    db.add_all(
        QualityListEntry(
            account_id=account_id,
            name=name,
            origin=QualityOrigin.built_in,
            position=position,
        )
        for position, name in enumerate(BUILT_IN_QUALITIES)
    )


async def listing(db: AsyncSession, account_id: uuid.UUID) -> list[QualityListEntry]:
    """The account's list in list order, which is also the criteria rotation order."""
    rows = await db.scalars(
        select(QualityListEntry)
        .where(QualityListEntry.account_id == account_id)
        .order_by(QualityListEntry.position, QualityListEntry.created_at, QualityListEntry.id)
    )
    return list(rows)
