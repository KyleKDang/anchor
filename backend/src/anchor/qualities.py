"""The account's quality list: the built-in dozen, seeded, and what rotates through it.

One canonical list per account sits behind both the quality picker and criteria
questions. It is seeded with the built-in vocabulary the moment the account becomes
real, so nothing downstream ever has to cope with an account that has no qualities yet,
and owner additions arriving later are ordinary entries on the same list - askable, and
treated identically everywhere.

The system never invents entries. Everything on the list is either the closed built-in
vocabulary or something the owner typed into the picker's free text.
"""

import uuid

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from anchor.models import BUILT_IN_QUALITIES, QualityListEntry, QualityOrigin

NAME_LIMIT = 64
"""The column's width, and so the longest a custom quality may be.

A quality is a word or two - "Worldbuilding", "Emotional impact" - because it has to fit
inside "Which had the better ___?" and be recognisable on a checkbox. The limit is the
schema's, restated here so a name that would not fit is refused rather than truncated.
"""


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


async def add_custom(db: AsyncSession, account_id: uuid.UUID, name: str) -> QualityListEntry | None:
    """Put an owner-typed quality on the list, or hand back the one that already says it.

    The picker's free-text escape hatch, and the only way an entry is ever added after
    seeding. It lands after everything present, so the criteria rotation reaches it in
    turn like any other entry rather than jumping the queue.

    A name the list already carries returns that entry instead of failing. The owner
    typing "acting" has not made a mistake to be told about - they have named a quality
    they already have, and the useful answer is to hand it to them. Returns None where
    the name is not a name at all, which the endpoint turns into a refusal.
    """
    tidied = " ".join(name.split())
    if not tidied or len(tidied) > NAME_LIMIT:
        return None
    existing = await db.scalar(
        select(QualityListEntry).where(
            QualityListEntry.account_id == account_id,
            func.lower(QualityListEntry.name) == tidied.lower(),
        )
    )
    if existing is not None:
        return existing
    last = await db.scalar(
        select(func.max(QualityListEntry.position)).where(QualityListEntry.account_id == account_id)
    )
    entry = QualityListEntry(
        account_id=account_id,
        name=tidied,
        origin=QualityOrigin.custom,
        position=(last if last is not None else -1) + 1,
    )
    db.add(entry)
    await db.flush()
    return entry


async def record_suggestions(db: AsyncSession, account_id: uuid.UUID, names: list[str]) -> None:
    """Replace what Anchor guesses this owner cares about with the newest guess.

    Wholesale rather than additive, because the guess is a reading of the account as it
    stands: a quality the newest evidence no longer supports should stop being pre-ticked,
    and a set that only ever grew would end up ticking the entire list.
    """
    wanted = [name.casefold() for name in names]
    mine = QualityListEntry.account_id == account_id
    guessed = func.lower(QualityListEntry.name).in_(wanted)
    await db.execute(update(QualityListEntry).where(mine, ~guessed).values(suggested_at=None))
    await db.execute(update(QualityListEntry).where(mine, guessed).values(suggested_at=func.now()))


async def clear_suggestions(db: AsyncSession, account_id: uuid.UUID) -> None:
    """Drop every guess, for the moment the owner answers and there is nothing left to guess."""
    await db.execute(
        update(QualityListEntry)
        .where(QualityListEntry.account_id == account_id)
        .values(suggested_at=None)
    )
