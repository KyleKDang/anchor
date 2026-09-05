"""The prose profile: when it is worth rewriting, what it is written from, and its versions.

The owner-readable third of the taste profile (ADR 0004). The weight vector and the
exemplar set are recomputed on every ordering change because they cost milliseconds;
this one costs money, so it is regenerated on *accumulated* change instead - and never
per comparison, which is the difference between a taste engine and a meter running.

*Accumulated change is measured, not guessed.* Every version row carries the watermark
of what the account looked like when it was written, and :func:`due` compares the
account's current state against the newest one. The four dimensions are taste-profile.md's
own triggers - new placements, an anchor change, a drift-resolution wave, a picker or
constraint edit - plus a staleness backstop denominated in answered comparisons, for the
owner whose settling work never lands a new placement.

*No calendar anywhere.* Spend is earned by engagement, so nothing here reads a clock: a
dormant account is never due, however long it has been dormant, and an owner who rates
fifty films in a weekend is due exactly as often as one who took a year over them.

*Nothing here imports the LLM module.* This module is the db half - what to write from
and when - so the web process can read the live version off it for the Profile screen.
The call itself is made in the worker, by the job that imports the seam.
"""

import hashlib
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from anchor import readiness
from anchor.models import (
    AnchorDesignation,
    AnchorStatus,
    ComparisonKind,
    ComparisonLogEntry,
    ComparisonStatus,
    ComparisonVerdict,
    ConstraintKind,
    DriftFlag,
    Exemplar,
    ExemplarRole,
    Film,
    Placement,
    ProfileConstraint,
    ProseProfileVersion,
    ProseTrigger,
    QualityListEntry,
)
from anchor.settings import Settings

CRITERIA_EVIDENCE = 30
"""Answered bonus questions carried into a regeneration, newest first.

A cap rather than the whole log: the newest answers describe the taste the owner has
now, and a prompt that grew with the account would cost more every month for evidence
that says the same thing.
"""


@dataclass(frozen=True)
class Evidence:
    """Everything a regeneration is allowed to read about an account's taste.

    Already phrased, because the phrasing is a judgment about what the account's rows
    mean - which film stands for which band, which answer is worth quoting - and that
    judgment belongs beside the queries that made it rather than inside a prompt string.
    """

    anchors: Sequence[str]
    """The owner's canonical film per band, best band first: the calibration points."""
    loved: Sequence[str]
    disliked: Sequence[str]
    """The ordering's two ends, closest to the end first."""
    criteria: Sequence[str]
    """Answered bonus questions, as "Quality: this film over that one" lines (ADR 0007)."""
    constraints: Sequence[str]
    """What the owner has said about themselves outright. Instructions, not evidence."""
    rated_films: int
    explicit_comparisons: int


@dataclass(frozen=True)
class Watermark:
    """What the account looked like when a version was written.

    Three counters that only go up, and two digests. Anchors and constraints are
    current-only - designating over one clears the old row - so no count reliably moves
    when they change, and a digest catches the swap that leaves the count alone.
    """

    placements: int
    explicit_comparisons: int
    drift_resolutions: int
    anchors: str
    constraints: str


async def latest(db: AsyncSession, account_id: uuid.UUID) -> ProseProfileVersion | None:
    """The live prose: the highest version this account has. None before the first one."""
    live: ProseProfileVersion | None = await db.scalar(
        select(ProseProfileVersion)
        .where(ProseProfileVersion.account_id == account_id)
        .order_by(ProseProfileVersion.version.desc())
        .limit(1)
    )
    return live


async def due(db: AsyncSession, account_id: uuid.UUID, settings: Settings) -> ProseTrigger | None:
    """What has accumulated far enough to be worth a regeneration, or None.

    Checked twice in a regeneration's life: once when a retrain queues the job, so an
    account that is not due costs no job, and once inside the job, because another
    regeneration may have landed while this one waited its turn on the account lock.

    A cold account is never due. That is the same rule the seam enforces structurally
    before it spends, restated here only so nothing is queued that would be refused.
    """
    if await readiness.state(db, account_id, settings) is readiness.Readiness.cold:
        return None
    live = await latest(db, account_id)
    if live is None:
        return ProseTrigger.first

    now = await watermark(db, account_id)
    # The set-shaped triggers first, and at any size of change: designating an anchor or
    # answering the picker is the owner saying something directly about their taste,
    # which outranks any amount of ordering movement they said nothing about.
    if now.anchors != live.anchors:
        return ProseTrigger.anchors
    if now.constraints != live.constraints:
        return ProseTrigger.constraints
    if now.drift_resolutions - live.drift_resolutions >= settings.prose_drift_trigger:
        return ProseTrigger.drift
    if now.placements - live.placements >= settings.prose_placements_trigger:
        return ProseTrigger.placements
    if now.explicit_comparisons - live.explicit_comparisons >= settings.prose_staleness_comparisons:
        return ProseTrigger.staleness
    return None


async def record(
    db: AsyncSession,
    account_id: uuid.UUID,
    *,
    text: str,
    trigger: ProseTrigger,
    mark: Watermark,
) -> ProseProfileVersion:
    """Append the new version, numbered one past the account's highest.

    The number is read rather than counted so a gap could never renumber anything, and
    the account lock the regeneration job runs under is what keeps two regenerations from
    reading the same highest at once - the unique constraint refuses it if they do.

    The watermark passed in is the one read *before* the call, not after: the version
    describes the account the prompt saw, and anything the owner did while the provider
    was thinking is change the next regeneration should count.
    """
    highest = await db.scalar(
        select(func.max(ProseProfileVersion.version)).where(
            ProseProfileVersion.account_id == account_id
        )
    )
    version = ProseProfileVersion(
        account_id=account_id,
        version=(highest or 0) + 1,
        text=text,
        trigger=trigger,
        placements=mark.placements,
        explicit_comparisons=mark.explicit_comparisons,
        drift_resolutions=mark.drift_resolutions,
        anchors=mark.anchors,
        constraints=mark.constraints,
    )
    db.add(version)
    return version


async def watermark(db: AsyncSession, account_id: uuid.UUID) -> Watermark:
    """The account as the trigger check reads it, right now."""
    counted = await readiness.evidence(db, account_id)
    placements = await db.scalar(
        select(func.count()).select_from(Placement).where(Placement.account_id == account_id)
    )
    resolutions = await db.scalar(
        select(func.count())
        .select_from(DriftFlag)
        .where(DriftFlag.account_id == account_id, DriftFlag.closed_at.is_not(None))
    )
    return Watermark(
        placements=int(placements or 0),
        explicit_comparisons=counted.explicit_comparisons,
        drift_resolutions=int(resolutions or 0),
        anchors=await _anchor_digest(db, account_id),
        constraints=await _constraint_digest(db, account_id),
    )


async def evidence(db: AsyncSession, account_id: uuid.UUID) -> Evidence:
    """What the account's rows say about its owner, phrased for a prompt.

    Read off the artifacts that are already kept current rather than recomputed here:
    the exemplar set is rewritten by every retrain, so the films standing for this taste
    are whatever the last ordering change made them.
    """
    counted = await readiness.evidence(db, account_id)
    exemplars = await _exemplars(db, account_id)
    return Evidence(
        anchors=[
            f"{row.band:.1f} stars: {_titled(film)}"
            for row, film in exemplars
            if row.role is ExemplarRole.anchor and row.band is not None
        ],
        loved=[_titled(film) for row, film in exemplars if row.role is ExemplarRole.best],
        disliked=[_titled(film) for row, film in exemplars if row.role is ExemplarRole.worst],
        criteria=await _criteria_lines(db, account_id),
        constraints=await _constraint_lines(db, account_id),
        rated_films=counted.rated_films,
        explicit_comparisons=counted.explicit_comparisons,
    )


async def active_constraints(db: AsyncSession, account_id: uuid.UUID) -> list[ProfileConstraint]:
    """The constraints every regeneration must respect: those the owner has not lifted."""
    rows = await db.scalars(
        select(ProfileConstraint)
        .where(
            ProfileConstraint.account_id == account_id,
            ProfileConstraint.lifted_at.is_(None),
        )
        .order_by(ProfileConstraint.created_at, ProfileConstraint.id)
    )
    return list(rows)


# --- Reads ---


async def _anchor_digest(db: AsyncSession, account_id: uuid.UUID) -> str:
    """The current anchors as one comparable value: which film stands for which band.

    Keyed on the band and the film rather than on the designation row, so retiring an
    anchor and designating the same film to the same band again - which is a real thing
    the anchor screen lets an owner do - is correctly no change at all.
    """
    rows = await db.execute(
        select(AnchorDesignation.band, AnchorDesignation.account_film_id).where(
            AnchorDesignation.account_id == account_id,
            AnchorDesignation.status == AnchorStatus.current,
        )
    )
    return _digest(f"{band}:{account_film_id}" for band, account_film_id in rows)


async def _constraint_digest(db: AsyncSession, account_id: uuid.UUID) -> str:
    """The active constraints as one comparable value.

    Keyed on the row, because lifting one and adding another are both changes the owner
    made deliberately, and a constraint re-stated after being lifted is a fresh row.
    """
    rows = await db.scalars(
        select(ProfileConstraint.id).where(
            ProfileConstraint.account_id == account_id,
            ProfileConstraint.lifted_at.is_(None),
        )
    )
    return _digest(str(row) for row in rows)


def _digest(parts: Iterable[str]) -> str:
    """A stable digest of a set of strings; sorted, so the rows may arrive in any order."""
    joined = "\n".join(sorted(parts))
    return hashlib.sha256(joined.encode()).hexdigest()


async def _exemplars(db: AsyncSession, account_id: uuid.UUID) -> list[tuple[Exemplar, Film]]:
    """The exemplar set with its catalog rows, best band and closest-to-the-end first."""
    rows = await db.execute(
        select(Exemplar, Film)
        .join(Film, Film.tmdb_id == Exemplar.film_id)
        .where(Exemplar.account_id == account_id)
        .order_by(Exemplar.role, Exemplar.rank)
    )
    return [(exemplar, film) for exemplar, film in rows]


async def _criteria_lines(db: AsyncSession, account_id: uuid.UUID) -> list[str]:
    """Answered bonus questions as evidence lines, newest first (ADR 0007).

    Answers only: a criteria row is born ``skip`` when the card is offered, and an
    unanswered offer is the owner declining to judge rather than a judgment. A tie is
    kept - "these two are level on pacing" says something a missing row does not.
    """
    a_film, b_film = aliased(Film), aliased(Film)
    rows = await db.execute(
        select(
            QualityListEntry.name,
            ComparisonLogEntry.verdict,
            a_film.title,
            a_film.release_year,
            b_film.title,
            b_film.release_year,
        )
        .join(QualityListEntry, QualityListEntry.id == ComparisonLogEntry.quality_id)
        .join(a_film, a_film.tmdb_id == ComparisonLogEntry.film_a_id)
        .join(b_film, b_film.tmdb_id == ComparisonLogEntry.film_b_id)
        .where(
            ComparisonLogEntry.account_id == account_id,
            ComparisonLogEntry.kind == ComparisonKind.criteria,
            ComparisonLogEntry.status == ComparisonStatus.active,
            ComparisonLogEntry.verdict != ComparisonVerdict.skip,
        )
        .order_by(ComparisonLogEntry.created_at.desc(), ComparisonLogEntry.id)
        .limit(CRITERIA_EVIDENCE)
    )
    return [
        _criteria_line(quality, verdict, _title(a, a_year), _title(b, b_year))
        for quality, verdict, a, a_year, b, b_year in rows
    ]


def _criteria_line(quality: str, verdict: ComparisonVerdict, a: str, b: str) -> str:
    if verdict is ComparisonVerdict.tied:
        return f"{quality}: {a} and {b} are level"
    winner, loser = (a, b) if verdict is ComparisonVerdict.a else (b, a)
    return f"{quality}: {winner} over {loser}"


async def _constraint_lines(db: AsyncSession, account_id: uuid.UUID) -> list[str]:
    """Active constraints, phrased as the standing instructions they are.

    A picker selection reads as a quality the owner has said they care about; a prose
    correction reads as the claim they thumbed down, so a regeneration can avoid making
    it again rather than merely knowing it was disliked.
    """
    lines = []
    names = await _quality_names(db, account_id)
    for constraint in await active_constraints(db, account_id):
        if constraint.kind is ConstraintKind.quality_pick and constraint.quality_id is not None:
            lines.append(f"They have said they care about: {names[constraint.quality_id]}")
        elif constraint.content is not None:
            claim = str(constraint.content.get("claim") or "").strip()
            if claim:
                lines.append(f"They have said this is wrong about them: {claim}")
    return lines


async def _quality_names(db: AsyncSession, account_id: uuid.UUID) -> dict[uuid.UUID, str]:
    rows = await db.execute(
        select(QualityListEntry.id, QualityListEntry.name).where(
            QualityListEntry.account_id == account_id
        )
    )
    return {entry_id: name for entry_id, name in rows}


def _titled(film: Film) -> str:
    return _title(film.title, film.release_year)


def _title(title: str, year: int | None) -> str:
    return f"{title} ({year})" if year else title
