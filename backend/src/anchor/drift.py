"""Drift: judgments that contradict the ordering, and what the owner does about them.

Drift is the condition where later judgments disagree with where a film sits. The hard
wall (rating-system.md) is that noticing it changes nothing: a contradicting answer is
stored, marked in tension, and raises a flag on one of the two films. Nothing moves
until the owner re-places the film themselves.

Four things make this module what it is:

*A contradiction is read off the ordering, never off the log.* The log is evidence, not
an event source (ADR 0010), so "does this judgment still hold?" is always answered by
comparing the two films' current slots - which is why the same judgment can fall in and
out of tension as the films around it move, without anybody rewriting it.

*The flag sits on the film, not on the judgment.* Several judgments can implicate one
film and the owner should resolve the film once, so the flag aggregates them. Which of
the two films it lands on is the one advisory reading this module makes: the least
trusted position, since that is the one more likely to be the bent ruler.

*Quiet before loud.* A thin suspicion buys one targeted question slipped into a normal
comparison moment, indistinguishable from any other. Only evidence that outruns what
noise explains surfaces the flag, and surfacing is as far as escalation ever goes: the
film is benched as an opponent, and nothing is blocked, moved, or pushed.

*A divider move is not drift.* Dividers move under films without reordering them, so a
rating that flips because the scale shifted raises nothing here - the sweep runs on
films that actually changed places, and a divider move changes nobody's place.
"""

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from anchor import anchors as anchors_module
from anchor import ordering as ordering_module
from anchor.accounts import CurrentAccount
from anchor.catalog import FilmCard
from anchor.deps import DbSession
from anchor.errors import ApiError
from anchor.models import (
    AccountFilm,
    ComparisonContext,
    ComparisonKind,
    ComparisonLogEntry,
    ComparisonStatus,
    ComparisonVerdict,
    DriftEvidence,
    DriftFlag,
    DriftOutcome,
    DriftStage,
    LifecycleState,
    Placement,
    PlacementTrust,
)
from anchor.ordering import Ordering

router = APIRouter(prefix="/api/drift")


# --- Reading a judgment against the ordering ---


def holds(ordering: Ordering, entry: ComparisonLogEntry, seeded: set[uuid.UUID]) -> bool | None:
    """Whether this judgment still agrees with where the two films sit, or None if it is mute.

    Mute covers everything that says nothing about the ordering: a band judgment, a skip,
    and a comparison against a film that is not rated. A strict verdict between two films
    sharing an import-seeded slot is mute too - that group is a placeholder the import
    put them in, never a judgment that they are equal, so contradicting it contradicts
    nobody.
    """
    if entry.kind is not ComparisonKind.overall or entry.verdict is ComparisonVerdict.skip:
        return None
    if entry.verdict is None or entry.film_b_id is None:
        return None
    above = ordering.index_of(entry.film_a_id)
    below = ordering.index_of(entry.film_b_id)
    if above is None or below is None:
        return None
    if entry.verdict is ComparisonVerdict.tied:
        return above == below
    if above == below:
        if ordering.slots[above].id in seeded:
            return None
        return False
    winner_first = entry.verdict is ComparisonVerdict.a
    return (above < below) is winner_first


def films_of(entry: ComparisonLogEntry) -> tuple[int, int]:
    """The pair a comparison is about. Only ever called where both films exist."""
    assert entry.film_b_id is not None  # a comparison names two films
    return entry.film_a_id, entry.film_b_id


def _won(entry: ComparisonLogEntry) -> int | None:
    """The film the owner put on top, or None where they put neither there."""
    if entry.verdict is ComparisonVerdict.a:
        return entry.film_a_id
    if entry.verdict is ComparisonVerdict.b:
        return entry.film_b_id
    return None


# --- The sweep ---


async def resweep(db: AsyncSession, account_id: uuid.UUID, film_ids: Iterable[int]) -> None:
    """Re-read every live judgment touching these films, and raise or close flags to match.

    Run after a flow that moved a film in the ordering, or that recorded a judgment
    between two films already in it. Judgments that have started to contradict go into
    tension and feed a flag; ones that have stopped come back to active and take their
    evidence with them, which is how a flag whose evidence resolves on its own closes.

    Superseded judgments are never revived: superseded means the owner settled against
    it, and that decision outlives any amount of later shuffling.
    """
    subjects = list(dict.fromkeys(film_ids))
    if not subjects:
        return
    await db.flush()
    ordering = await ordering_module.load(db, account_id)
    seeded = await ordering_module.seeded_slot_ids(db, account_id)
    entries = await _live_entries(db, account_id, subjects)
    attached = await _attachments(db, [entry.id for entry in entries])

    touched: set[uuid.UUID] = set()
    for entry in entries:
        standing = holds(ordering, entry, seeded)
        if standing is None:
            continue
        if standing:
            entry.status = ComparisonStatus.active
            if (evidence := attached.get(entry.id)) is not None:
                touched.add(evidence.flag_id)
                await db.delete(evidence)
            continue
        entry.status = ComparisonStatus.in_tension
        if entry.id in attached:
            continue  # already hanging on a flag, which may be the opponent's
        flag = await _flag_for(db, account_id, entry)
        db.add(DriftEvidence(account_id=account_id, flag_id=flag.id, entry_id=entry.id))
        touched.add(flag.id)

    await db.flush()
    await _restage(db, account_id, touched)


async def settle(db: AsyncSession, account_id: uuid.UUID, film_id: int) -> None:
    """Re-read one film's judgments after a re-placement, and close the flag that drove it.

    The resolution rule, which is not the sweep's: the owner has just answered their way
    to a new position, so a judgment that disagrees with it has been settled against and
    is superseded, rather than left in tension to raise the same flag again. Consistent
    ones come back to active, and the flag closes having done its whole job.

    Either way the judgment stops being in tension, so it stops being evidence - and it
    may have been evidence for the *opponent's* flag rather than this one. That is the
    self-resolving case the spec names: a film moves, the judgments about it come good,
    and a flag somewhere else quietly runs out of things to stand on.
    """
    await db.flush()
    ordering = await ordering_module.load(db, account_id)
    seeded = await ordering_module.seeded_slot_ids(db, account_id)
    entries = await _live_entries(db, account_id, [film_id])
    attached = await _attachments(db, [entry.id for entry in entries])

    released: set[uuid.UUID] = set()
    for entry in entries:
        standing = holds(ordering, entry, seeded)
        if standing is None:
            continue
        entry.status = ComparisonStatus.active if standing else ComparisonStatus.superseded
        if (evidence := attached.get(entry.id)) is not None:
            released.add(evidence.flag_id)
            await db.delete(evidence)

    await db.flush()
    flag = await open_flag(db, account_id, film_id)
    if flag is not None:
        await close(db, flag, DriftOutcome.re_placed)
        released.discard(flag.id)
    await _restage(db, account_id, released)


async def close(db: AsyncSession, flag: DriftFlag, outcome: DriftOutcome) -> None:
    """Close a flag and drop its evidence rows; the judgments keep their own statuses."""
    flag.closed_at = func.now()
    flag.outcome = outcome
    flag.re_placing_since = None
    await db.execute(delete(DriftEvidence).where(DriftEvidence.flag_id == flag.id))


async def _restage(db: AsyncSession, account_id: uuid.UUID, flag_ids: set[uuid.UUID]) -> None:
    """Close the flags left with no evidence, and surface the ones noise cannot explain.

    Surfacing is one-way. A flag the owner has already been shown does not slip back into
    the quiet phase because one judgment resolved itself: they are mid-decision, and a
    doubt that vanishes off the strip while being read is worse than one that waits.
    """
    if not flag_ids:
        return
    rows = await db.execute(
        select(DriftEvidence.flag_id, func.count())
        .where(DriftEvidence.flag_id.in_(flag_ids))
        .group_by(DriftEvidence.flag_id)
    )
    counts: dict[uuid.UUID, int] = {flag_id: standing for flag_id, standing in rows}
    flags = await db.scalars(
        select(DriftFlag).where(DriftFlag.id.in_(flag_ids), DriftFlag.closed_at.is_(None))
    )
    for flag in flags:
        standing = counts.get(flag.id, 0)
        if standing == 0:
            await close(db, flag, DriftOutcome.self_resolved)
        elif standing >= _SURFACE_AT:
            flag.stage = DriftStage.surfaced


_SURFACE_AT = 2
"""In-tension judgments a flag needs before it stops being explainable as noise.

One answer against the ordering is a slip of the finger as often as it is a change of
mind, and the quiet drift check exists precisely to find out which. Two independent
contradictions is where "they keep saying this" starts, so that is where the owner is
asked. Tuning, not spec: rating-system.md fixes the two phases, not the number.
"""


async def _flag_for(
    db: AsyncSession, account_id: uuid.UUID, entry: ComparisonLogEntry
) -> DriftFlag:
    """The open flag this contradiction feeds, opening one if the film has none yet."""
    film_id = await _least_trusted(db, account_id, films_of(entry))
    flag = await open_flag(db, account_id, film_id)
    if flag is not None:
        return flag
    account_film = await _account_film(db, account_id, film_id)
    flag = DriftFlag(
        account_id=account_id,
        account_film_id=account_film.id,
        stage=DriftStage.quiet,
    )
    db.add(flag)
    await db.flush()
    return flag


async def _least_trusted(db: AsyncSession, account_id: uuid.UUID, pair: tuple[int, int]) -> int:
    """Which of the two films the advisory math trusts least, which is where the flag lands.

    Two readings, in order. A provisional position is trusted less than a full one by
    definition - that is what the trust field means - and between two positions of equal
    standing, the one resting on fewer of the owner's own answers is the softer of the
    two. The film id breaks the last tie so the same contradiction always lands the same
    way, because which film the owner is asked about is owner-visible behaviour and must
    not depend on which row came back first.
    """
    trust = await _trust(db, account_id, pair)
    answers = await _answer_counts(db, account_id, pair)
    return min(
        pair,
        key=lambda film_id: (
            trust.get(film_id) is not PlacementTrust.provisional,
            answers.get(film_id, 0),
            film_id,
        ),
    )


async def _trust(
    db: AsyncSession, account_id: uuid.UUID, film_ids: Sequence[int]
) -> dict[int, PlacementTrust]:
    rows = await db.execute(
        select(AccountFilm.film_id, Placement.trust)
        .join(Placement, Placement.account_film_id == AccountFilm.id)
        .where(AccountFilm.account_id == account_id, AccountFilm.film_id.in_(film_ids))
    )
    return {film_id: trust for film_id, trust in rows}


async def _answer_counts(
    db: AsyncSession, account_id: uuid.UUID, film_ids: Sequence[int]
) -> dict[int, int]:
    """How many live judgments each film's position rests on, however they were produced."""
    counts: dict[int, int] = {}
    for film_id in film_ids:
        counts[film_id] = (
            await db.scalar(
                select(func.count())
                .select_from(ComparisonLogEntry)
                .where(
                    ComparisonLogEntry.account_id == account_id,
                    ComparisonLogEntry.kind == ComparisonKind.overall,
                    ComparisonLogEntry.status == ComparisonStatus.active,
                    ComparisonLogEntry.verdict != ComparisonVerdict.skip,
                    or_(
                        ComparisonLogEntry.film_a_id == film_id,
                        ComparisonLogEntry.film_b_id == film_id,
                    ),
                )
            )
        ) or 0
    return counts


async def _live_entries(
    db: AsyncSession, account_id: uuid.UUID, film_ids: Sequence[int]
) -> list[ComparisonLogEntry]:
    """Every judgment touching these films that could still change standing, oldest first."""
    rows = await db.scalars(
        select(ComparisonLogEntry)
        .where(
            ComparisonLogEntry.account_id == account_id,
            ComparisonLogEntry.kind == ComparisonKind.overall,
            ComparisonLogEntry.status.in_([ComparisonStatus.active, ComparisonStatus.in_tension]),
            or_(
                ComparisonLogEntry.film_a_id.in_(film_ids),
                ComparisonLogEntry.film_b_id.in_(film_ids),
            ),
        )
        .order_by(ComparisonLogEntry.created_at, ComparisonLogEntry.id)
    )
    return list(rows)


async def _attachments(
    db: AsyncSession, entry_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, DriftEvidence]:
    if not entry_ids:
        return {}
    rows = await db.scalars(select(DriftEvidence).where(DriftEvidence.entry_id.in_(entry_ids)))
    return {row.entry_id: row for row in rows}


# --- Reading flags ---


async def open_flag(db: AsyncSession, account_id: uuid.UUID, film_id: int) -> DriftFlag | None:
    flag: DriftFlag | None = await db.scalar(
        select(DriftFlag)
        .join(AccountFilm, AccountFilm.id == DriftFlag.account_film_id)
        .where(
            DriftFlag.account_id == account_id,
            DriftFlag.closed_at.is_(None),
            AccountFilm.film_id == film_id,
        )
    )
    return flag


async def surfaced(db: AsyncSession, account_id: uuid.UUID) -> dict[int, DriftFlag]:
    """Every flag the owner can see, by film: the needs-attention strip's whole contents."""
    rows = await db.execute(
        select(AccountFilm.film_id, DriftFlag)
        .join(AccountFilm, AccountFilm.id == DriftFlag.account_film_id)
        .where(
            DriftFlag.account_id == account_id,
            DriftFlag.closed_at.is_(None),
            DriftFlag.stage == DriftStage.surfaced,
        )
        .order_by(DriftFlag.opened_at, AccountFilm.film_id)
    )
    return {film_id: flag for film_id, flag in rows}


async def benched(db: AsyncSession, account_id: uuid.UUID) -> frozenset[int]:
    """Films no other placement may be measured against: a doubted position is a bent ruler."""
    return frozenset(await surfaced(db, account_id))


async def evidence_of(db: AsyncSession, flag: DriftFlag) -> list[ComparisonLogEntry]:
    """The in-tension judgments this flag stands on, oldest first."""
    rows = await db.scalars(
        select(ComparisonLogEntry)
        .join(DriftEvidence, DriftEvidence.entry_id == ComparisonLogEntry.id)
        .where(DriftEvidence.flag_id == flag.id)
        .order_by(ComparisonLogEntry.created_at, ComparisonLogEntry.id)
    )
    return list(rows)


async def seeding_evidence(
    db: AsyncSession, account_id: uuid.UUID, film_id: int
) -> list[ComparisonLogEntry]:
    """The in-tension judgments a re-placement resumes from, oldest first.

    Strict verdicts only. A tie is a definitive answer that ends a search outright, so
    seeding one would not head-start the re-placement, it would decide it - and the
    owner opened the flow to look again, not to have last month's answer applied for
    them. The tie stays in the evidence and is settled with everything else on landing.
    """
    flag = await open_flag(db, account_id, film_id)
    if flag is None:
        return []
    return [
        entry
        for entry in await evidence_of(db, flag)
        if entry.verdict in (ComparisonVerdict.a, ComparisonVerdict.b)
    ]


async def surface_at_rewatch(db: AsyncSession, account_id: uuid.UUID, film_id: int) -> None:
    """Show a quietly-held flag now, because a rewatch is when the owner is thinking of it.

    Every other route to the surface waits for evidence to pile up. This one waits for
    the right moment instead: the owner has just watched the film again and is already
    asking themselves whether it still feels the same, which is the question the flag
    wanted asked. Nothing about the evidence changes - only when it is shown.
    """
    flag = await open_flag(db, account_id, film_id)
    if flag is not None:
        flag.stage = DriftStage.surfaced


async def replacing_since(db: AsyncSession, account_id: uuid.UUID, film_id: int) -> datetime | None:
    """When the owner sent this film into a re-placement from its drift flag, if they did."""
    flag = await open_flag(db, account_id, film_id)
    return flag.re_placing_since if flag is not None else None


# --- The quiet drift check ---


@dataclass(frozen=True)
class Suspicion:
    """One pending doubt worth one question, and the flag that would hear the answer."""

    flag_id: uuid.UUID
    film_id: int
    """The flagged film: the one whose position the question is really about."""
    opponent_id: int


async def pending_check(db: AsyncSession, account_id: uuid.UUID) -> Suspicion | None:
    """The oldest suspicion still worth probing, or None where every one has been asked.

    Oldest first, so a doubt raised weeks ago is not starved by a fresh one, and a pair
    already put to the owner is never put again: the answer is folded in the moment it
    arrives, and asking the same question twice on the same evidence is the nagging the
    posture forbids (ADR 0011).
    """
    rows = await db.execute(
        select(AccountFilm.film_id, DriftFlag.id, ComparisonLogEntry)
        .select_from(DriftFlag)
        .join(AccountFilm, AccountFilm.id == DriftFlag.account_film_id)
        .join(DriftEvidence, DriftEvidence.flag_id == DriftFlag.id)
        .join(ComparisonLogEntry, ComparisonLogEntry.id == DriftEvidence.entry_id)
        .where(
            DriftFlag.account_id == account_id,
            DriftFlag.closed_at.is_(None),
            DriftFlag.stage == DriftStage.quiet,
        )
        .order_by(DriftFlag.opened_at, ComparisonLogEntry.created_at, ComparisonLogEntry.id)
    )
    probed = await _probed_pairs(db, account_id)
    for film_id, flag_id, entry in rows:
        pair = films_of(entry)
        opponent = pair[1] if pair[0] == film_id else pair[0]
        if frozenset(pair) in probed:
            continue
        return Suspicion(flag_id=flag_id, film_id=film_id, opponent_id=opponent)
    return None


async def _probed_pairs(db: AsyncSession, account_id: uuid.UUID) -> set[frozenset[int]]:
    """Every pair a drift check has already put to the owner, however they answered."""
    rows = await db.execute(
        select(ComparisonLogEntry.film_a_id, ComparisonLogEntry.film_b_id).where(
            ComparisonLogEntry.account_id == account_id,
            ComparisonLogEntry.context == ComparisonContext.drift_check,
        )
    )
    return {frozenset((a, b)) for a, b in rows if b is not None}


# --- Wire shapes ---


class DriftJudgment(BaseModel):
    """One contradicting judgment, said in the owner's own terms rather than the log's."""

    opponent: FilmCard
    opponent_won: bool
    """The owner put the opponent above the flagged film, against where the two now sit."""
    tied: bool
    answered_at: datetime


class DriftFlagView(BaseModel):
    """An open, surfaced flag as the film page shows it, with what it stands on."""

    judgments: list[DriftJudgment]
    re_placing: bool
    """A re-placement is already running for this film, so the page resumes it."""
    anchor_warning: bool
    """Re-placing would risk this film's anchor status, so the offer says so upfront."""


class KeepOpponent(BaseModel):
    """What the owner said about one implicated opponent when keeping the position."""

    opponent_tmdb_id: int
    resolution: Literal["noise", "re_point"] = "noise"


class Keep(BaseModel):
    """Keeping the position, with the light follow-up per opponent.

    An opponent the owner said nothing about is noise, which is the default the spec
    names: the common case is a slip of the finger, and it costs one tap to say so.
    """

    opponents: list[KeepOpponent] = []


# --- Resolution ---


@router.post("/{tmdb_id}/re-place", status_code=204)
async def re_place(tmdb_id: int, account: CurrentAccount, db: DbSession) -> None:
    """ "My opinion changed": send the film into a re-placement, head-started by the evidence.

    Nothing moves here. This only records which flow the owner thinks they are in, so
    the placement search - which keeps no state of its own - reads their answers under
    the right context when they start giving them.
    """
    flag = await _surfaced_flag(db, account.id, tmdb_id)
    flag.re_placing_since = func.now()
    await db.commit()


@router.post("/{tmdb_id}/keep", status_code=204)
async def keep(tmdb_id: int, body: Keep, account: CurrentAccount, db: DbSession) -> None:
    """ "Those judgments were noise": the position stands, and each opponent is settled.

    Noise supersedes the judgment, which is the log recording that the owner settled
    against it without pretending it was never made. Re-pointing keeps the tension and
    hands it to the opponent, whose own flag then has to answer for it - resolvable
    then or whenever the owner gets to it.
    """
    flag = await _surfaced_flag(db, account.id, tmdb_id)
    said = {item.opponent_tmdb_id: item.resolution for item in body.opponents}
    re_pointed = False
    for entry in await evidence_of(db, flag):
        pair = films_of(entry)
        opponent = pair[1] if pair[0] == tmdb_id else pair[0]
        if said.get(opponent, "noise") == "noise":
            entry.status = ComparisonStatus.superseded
            continue
        await _re_point(db, account.id, entry, opponent)
        re_pointed = True
    await close(db, flag, DriftOutcome.re_pointed if re_pointed else DriftOutcome.kept)
    await db.commit()


async def _re_point(
    db: AsyncSession, account_id: uuid.UUID, entry: ComparisonLogEntry, opponent: int
) -> None:
    """Hand one still-standing tension to the opponent's flag, opening one where needed."""
    await db.execute(delete(DriftEvidence).where(DriftEvidence.entry_id == entry.id))
    flag = await open_flag(db, account_id, opponent)
    if flag is None:
        account_film = await _account_film(db, account_id, opponent)
        flag = DriftFlag(
            account_id=account_id, account_film_id=account_film.id, stage=DriftStage.quiet
        )
        db.add(flag)
        await db.flush()
    db.add(DriftEvidence(account_id=account_id, flag_id=flag.id, entry_id=entry.id))
    await db.flush()
    await _restage(db, account_id, {flag.id})


# --- Reading one film's flag ---


async def view(db: AsyncSession, account_id: uuid.UUID, film_id: int) -> DriftFlagView | None:
    """The film page's drift panel, or None where the film has nothing the owner may see."""
    flag = await open_flag(db, account_id, film_id)
    if flag is None or flag.stage is not DriftStage.surfaced:
        return None
    entries = await evidence_of(db, flag)
    opponents = [
        pair[1] if pair[0] == film_id else pair[0]
        for pair in (films_of(entry) for entry in entries)
    ]
    cards = await ordering_module.cards(db, opponents)
    anchors = await anchors_module.current(db, account_id)
    return DriftFlagView(
        judgments=[
            DriftJudgment(
                opponent=cards[opponent],
                opponent_won=_won(entry) == opponent,
                tied=entry.verdict is ComparisonVerdict.tied,
                answered_at=entry.created_at,
            )
            for entry, opponent in zip(entries, opponents, strict=True)
            if opponent in cards
        ],
        re_placing=flag.re_placing_since is not None,
        anchor_warning=film_id in anchors.values(),
    )


# --- Helpers ---


async def _surfaced_flag(db: AsyncSession, account_id: uuid.UUID, film_id: int) -> DriftFlag:
    """The flag a resolution is answering, refusing one the owner was never shown."""
    flag = await open_flag(db, account_id, film_id)
    if flag is None or flag.stage is not DriftStage.surfaced:
        raise ApiError(409, "no_drift_flag", "That film has nothing waiting to be resolved.")
    return flag


async def _account_film(db: AsyncSession, account_id: uuid.UUID, film_id: int) -> AccountFilm:
    account_film = await db.scalar(
        select(AccountFilm).where(
            AccountFilm.account_id == account_id,
            AccountFilm.film_id == film_id,
            AccountFilm.state == LifecycleState.rated,
        )
    )
    assert account_film is not None  # a film in the ordering is a rated film
    return account_film
