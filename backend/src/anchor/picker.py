"""The two ways the owner states their taste outright, and the rows that keep it stated.

Everything else Anchor knows about an owner is inferred from what they placed. This is
the part they say directly, and the design's whole ambition for it is that it cost them
almost nothing: a checklist Anchor has already filled in from their own judgments, and a
thumb-down on any claim in the prose that is wrong about them. Confirm, do not author
(taste-profile.md).

*A guess is not a statement.* Anchor pre-ticks what it thinks the owner cares about, and
that guess lives on the quality list entry, not in the constraints. The owner ticking it
is what turns it into a constraint - and until they do, nothing here reaches a
regeneration as an instruction. The distinction matters because a regeneration that had
to respect its own guesses would be reading its own handwriting back as evidence.

*What the owner states is structural, never text.* The prose profile is rewritten from
scratch every time, so a correction kept as an edit to its text would last exactly until
the next rewrite. Kept as a row, it is an input the rewrite has to honour, and it
survives however many times the description is rebuilt.

*Nothing here is a gate.* The picker is skippable, answering it with nothing ticked is a
real answer, and no feature anywhere asks whether it was answered. The one thing that
does change is the prose: a picker or constraint edit is the owner saying something about
themselves, so it schedules a regeneration at once rather than waiting for the ordering
to accumulate change it will never make.
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from anchor import jobs
from anchor import prose as prose_module
from anchor import qualities as qualities_module
from anchor.accounts import CurrentAccount
from anchor.deps import AppJobs, DbSession
from anchor.errors import ApiError
from anchor.models import (
    Account,
    ConstraintKind,
    ProfileConstraint,
    QualityListEntry,
    QualityOrigin,
)

router = APIRouter(prefix="/api/profile")


class Quality(BaseModel):
    """One row of the picker: a quality, whether it is ticked, and whether Anchor ticked it."""

    id: uuid.UUID
    name: str
    origin: QualityOrigin
    """Built-in or the owner's own. Downstream the two are treated identically; the
    picker shows it only so a custom entry is recognisable as something they added."""
    checked: bool
    """What the checkbox shows: the owner's own selection once they have answered, and
    Anchor's guess before that. The rule lives here rather than in the screen so that
    every client shows the same thing and the seam can be tested on it."""
    suggested: bool
    """Whether Anchor guessed this one, so the screen can say the ticks are a guess."""


class Picker(BaseModel):
    """The quality picker, as the owner meets it."""

    answered: bool
    """Whether the owner has ever answered. False while the ticks are Anchor's guess."""
    qualities: list[Quality]


class Selection(BaseModel):
    """The owner's answer: the whole set they left ticked, and nothing about the rest."""

    quality_ids: list[uuid.UUID] = Field(default_factory=list)


class Claim(BaseModel):
    """A sentence in the prose profile the owner says is wrong about them."""

    claim: str = Field(min_length=1, max_length=1000)


class Correction(BaseModel):
    """A standing correction, as the Profile screen carries it."""

    id: uuid.UUID
    claim: str
    created_at: datetime


@router.get("/qualities")
async def picker(account: CurrentAccount, db: DbSession) -> Picker:
    """The account's quality list with the ticks already in place.

    Offered from the account's first day: the list is seeded at creation, so this is
    never a screen that has to explain why it is empty. Anchor catching up later with a
    guess changes nothing the owner has already said, because a guess is only ever shown
    where they have not.
    """
    listed = await qualities_module.listing(db, account.id)
    picked = await _picked(db, account.id)
    answered = account.qualities_picked_at is not None
    return Picker(
        answered=answered,
        qualities=[_shown(entry, picked, answered=answered) for entry in listed],
    )


@router.put("/qualities")
async def pick(
    body: Selection, account: CurrentAccount, db: DbSession, jobs_app: AppJobs
) -> Picker:
    """Answer the picker: what is ticked is a constraint, what is not is lifted.

    Replace rather than add, because that is what a multi-select is - the owner's answer
    is the whole set they left ticked, and unticking is how a selection is taken back. A
    lifted selection keeps its row: the owner changing their mind is itself a fact about
    their taste, and the row is what says when they did.

    Answering with nothing ticked writes no constraints at all, and is still an answer.
    That is the whole of "always skippable": there is no state in which the owner is
    stuck on this screen, and no feature that asks whether they got through it.
    """
    listed = {entry.id for entry in await qualities_module.listing(db, account.id)}
    wanted = set(body.quality_ids)
    unknown = wanted - listed
    if unknown:
        # Owner-scoped like every account-realm row: an id from another account is not a
        # quality this owner has, and saying so is the same answer as one that never was.
        raise ApiError(404, "no_such_quality", "That is not one of your qualities.")

    live = await _live_picks(db, account.id)
    for quality_id, constraint in live.items():
        if quality_id not in wanted:
            constraint.lifted_at = func.now()
    for quality_id in wanted - set(live):
        db.add(
            ProfileConstraint(
                account_id=account.id,
                kind=ConstraintKind.quality_pick,
                quality_id=quality_id,
            )
        )
    account.qualities_picked_at = func.now()
    # Nothing left to guess: the owner has said what they care about, so the pre-ticks
    # come off and no further refresh is bought for an answer that already exists.
    await qualities_module.clear_suggestions(db, account.id)
    await jobs.schedule_prose_check(db, jobs_app, account.id)
    await db.commit()
    await db.refresh(account)
    return await picker(account, db)


class CustomQuality(BaseModel):
    """The picker's free text: a quality the owner names themselves."""

    name: str = Field(min_length=1, max_length=qualities_module.NAME_LIMIT)


@router.post("/qualities", response_model=Quality)
async def add_quality(body: CustomQuality, account: CurrentAccount, db: DbSession) -> Quality:
    """Add a quality of the owner's own to their list. Optional, and never a selection.

    Adding is not ticking. The entry joins the list as an ordinary member - askable by the
    criteria rotation, offerable to the next guess - and whether the owner cares about it
    is answered by the picker like every other entry. Keeping one writer for selections is
    what makes "what is ticked is the whole answer" true.
    """
    entry = await qualities_module.add_custom(db, account.id, body.name)
    if entry is None:
        raise ApiError(422, "invalid_quality", "That is not a quality.")
    await db.commit()
    # Shown by the same rule the picker itself uses, because this may be a quality the
    # owner already had: typing a name they already carry hands back that entry, ticks
    # and all, rather than a fresh one that would read as unticked.
    return _shown(
        entry,
        await _picked(db, account.id),
        answered=account.qualities_picked_at is not None,
    )


@router.post("/constraints", response_model=Correction)
async def correct(
    body: Claim, account: CurrentAccount, db: DbSession, jobs_app: AppJobs
) -> Correction:
    """Thumb down a claim in the prose profile: it is wrong about them, and stays recorded.

    The claim is stored rather than the prose edited, which is the only version of this
    that works: the next regeneration rewrites the text wholesale, so an edit would last
    until the next rewrite and a row lasts until the owner lifts it.

    The prose version it was read on rides along as provenance. Nothing consumes it - the
    claim alone is what a regeneration is told - but a correction is the owner disagreeing
    with a specific piece of writing, and which one that was is not recoverable later.
    """
    claim = " ".join(body.claim.split())
    if not claim:
        raise ApiError(422, "invalid_claim", "That is not a claim.")
    live = await prose_module.latest(db, account.id)
    constraint = ProfileConstraint(
        account_id=account.id,
        kind=ConstraintKind.prose_correction,
        content={"claim": claim, "prose_version": live.version if live else None},
    )
    db.add(constraint)
    await jobs.schedule_prose_check(db, jobs_app, account.id)
    await db.commit()
    return Correction(id=constraint.id, claim=claim, created_at=constraint.created_at)


@router.delete("/constraints/{constraint_id}", status_code=204)
async def lift(
    constraint_id: uuid.UUID, account: CurrentAccount, db: DbSession, jobs_app: AppJobs
) -> Response:
    """Take a correction back. The row is lifted, never deleted.

    Corrections only. A picker selection is lifted by unticking it, so that the set of
    ticked qualities has exactly one writer and cannot disagree with itself.
    """
    constraint = await db.scalar(
        select(ProfileConstraint).where(
            ProfileConstraint.id == constraint_id,
            ProfileConstraint.account_id == account.id,
            ProfileConstraint.kind == ConstraintKind.prose_correction,
            ProfileConstraint.lifted_at.is_(None),
        )
    )
    if constraint is None:
        raise ApiError(404, "no_such_correction", "That correction is not standing.")
    constraint.lifted_at = func.now()
    await jobs.schedule_prose_check(db, jobs_app, account.id)
    await db.commit()
    return Response(status_code=204)


async def corrections(db: AsyncSession, account_id: uuid.UUID) -> list[Correction]:
    """The corrections still standing, for the Profile screen to show beside the prose.

    Correctable means visible: an undo the owner cannot find is not an undo, and a claim
    they thumbed down months ago is still shaping what they read today.
    """
    return [
        Correction(
            id=constraint.id,
            claim=str((constraint.content or {}).get("claim", "")),
            created_at=constraint.created_at,
        )
        for constraint in await prose_module.active_constraints(db, account_id)
        if constraint.kind is ConstraintKind.prose_correction
    ]


def _shown(entry: QualityListEntry, picked: set[uuid.UUID], *, answered: bool) -> Quality:
    """One quality as the picker shows it.

    The tick is the owner's own selection once they have answered, and Anchor's guess
    before that. The rule lives here rather than in the screen so every client shows the
    same thing, and so the seam the tests run at is the thing that decides it.
    """
    return Quality(
        id=entry.id,
        name=entry.name,
        origin=entry.origin,
        checked=(entry.id in picked) if answered else (entry.suggested_at is not None),
        suggested=entry.suggested_at is not None,
    )


async def _picked(db: AsyncSession, account_id: uuid.UUID) -> set[uuid.UUID]:
    return set(await _live_picks(db, account_id))


async def _live_picks(
    db: AsyncSession, account_id: uuid.UUID
) -> dict[uuid.UUID, ProfileConstraint]:
    """The owner's standing selections, by the quality each names."""
    rows = await db.scalars(
        select(ProfileConstraint).where(
            ProfileConstraint.account_id == account_id,
            ProfileConstraint.kind == ConstraintKind.quality_pick,
            ProfileConstraint.lifted_at.is_(None),
        )
    )
    return {
        constraint.quality_id: constraint
        for constraint in rows
        if constraint.quality_id is not None
    }


async def unanswered(db: AsyncSession, account_id: uuid.UUID) -> bool:
    """Whether the picker is still worth guessing at: the owner has never answered it.

    Read by the regeneration job, which is where the guess is bought. Once the owner has
    answered there is nothing left to guess, so nothing is spent guessing it again - and
    the guess can never quietly overwrite the answer, because it stops being made.
    """
    answered = await db.scalar(select(Account.qualities_picked_at).where(Account.id == account_id))
    return answered is None
