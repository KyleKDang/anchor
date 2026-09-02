"""Readiness: cold, forming, or ready, read off the evidence every time it is asked for.

Three states gate the recommendation features, and there is no time component anywhere in
them - an account that rates fifty films in a weekend is as ready as one that took a
year, and one that has not opened the app since March is exactly as ready as it was in
March. What gates is evidence.

*Never stored authoritatively.* There is no readiness column, in this module or anywhere
else: it is a pure function of four counts and the configured thresholds, computed at the
moment of asking. That is what makes it impossible for a surface to promise a feature the
evidence no longer supports, and it is why the append-only metrics row records the counts
rather than the verdict.

*The dimensions are spec; the numbers are tuning.* taste-profile.md fixes what readiness
looks at - rated-film count, explicit-comparison share, bands spanned - and leaves the
thresholds to implementation, so they live in ``Settings`` where an operator can move
them without a migration.

*Cold is honest, not broken.* A cold account is not failing; it has not told Anchor
enough yet, and every surface it gates says so plainly rather than showing an empty
ranked tier (onboarding-and-import.md).
"""

import enum
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from anchor import ordering as ordering_module
from anchor.models import (
    AccountFilm,
    ComparisonKind,
    ComparisonLogEntry,
    ComparisonStatus,
    ComparisonVerdict,
    LifecycleState,
    Placement,
    PlacementTrust,
)
from anchor.settings import Settings


class Readiness(enum.StrEnum):
    """What the account's evidence currently supports."""

    cold = "cold"
    """Too little signal to train anything: no discovery, no ranked tier."""
    forming = "forming"
    """Enough for a stable fit. Discovery lights up."""
    ready = "ready"
    """Enough explicit judgment that the fit is not carried by guesses. The tier unlocks."""


@dataclass(frozen=True)
class Evidence:
    """The counts readiness is derived from, and the metrics row's context columns."""

    rated_films: int
    explicit_comparisons: int
    """Overall comparisons the owner actually answered. A skip records no judgment."""
    settled_films: int
    """Rated films whose position rests on the owner's own comparisons.

    The data model records exactly this as a fully-trusted placement: a provisional one
    is import-seeded or bailed out of early, which is the position Anchor guessed rather
    than the position the owner settled.
    """
    bands_spanned: int
    """Distinct half-star bands the ordering currently derives into: the band structure."""

    @property
    def explicit_share(self) -> float:
        """How much of the library the owner's own comparisons actually account for."""
        return self.settled_films / self.rated_films if self.rated_films else 0.0


async def evidence(db: AsyncSession, account_id: uuid.UUID) -> Evidence:
    """Count what the account has told Anchor so far."""
    rated = await db.scalar(
        select(func.count())
        .select_from(AccountFilm)
        .where(AccountFilm.account_id == account_id, AccountFilm.state == LifecycleState.rated)
    )
    answered = await db.scalar(
        select(func.count())
        .select_from(ComparisonLogEntry)
        .where(
            ComparisonLogEntry.account_id == account_id,
            ComparisonLogEntry.kind == ComparisonKind.overall,
            ComparisonLogEntry.status == ComparisonStatus.active,
            ComparisonLogEntry.verdict != ComparisonVerdict.skip,
        )
    )
    settled = await db.scalar(
        select(func.count())
        .select_from(Placement)
        .where(Placement.account_id == account_id, Placement.trust == PlacementTrust.full)
    )
    derived = await ordering_module.derived_bands(db, account_id)
    return Evidence(
        rated_films=rated or 0,
        explicit_comparisons=answered or 0,
        settled_films=settled or 0,
        bands_spanned=len({band for band in derived.values() if band is not None}),
    )


def classify(evidence: Evidence, settings: Settings) -> Readiness:
    """Which state the evidence supports. Derived on every read, stored nowhere."""
    if not _forming(evidence, settings):
        return Readiness.cold
    if not _ready(evidence, settings):
        return Readiness.forming
    return Readiness.ready


def _forming(evidence: Evidence, settings: Settings) -> bool:
    """Enough films across enough bands for the fit to be stable rather than lucky."""
    return (
        evidence.rated_films >= settings.readiness_forming_films
        and evidence.bands_spanned >= settings.readiness_forming_bands
    )


def _ready(evidence: Evidence, settings: Settings) -> bool:
    """A real library, enough of it settled by real judgments, with band structure present.

    The band bar is restated rather than inherited from forming, so this reads as the
    spec's own sentence and so the Profile screen can answer "why am I not ready yet"
    from the ready row alone.
    """
    return (
        evidence.rated_films >= settings.readiness_ready_films
        and evidence.explicit_share >= settings.readiness_ready_explicit_share
        and evidence.bands_spanned >= settings.readiness_forming_bands
    )
