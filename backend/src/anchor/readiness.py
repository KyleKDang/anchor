"""Readiness: cold, forming, or ready, read off the evidence every time it is asked for.

Three states gate the recommendation features, and there is no time component anywhere in
them - an account that rates fifty films in a weekend is as ready as one that took a
year, and one that has not opened the app since March is exactly as ready as it was in
March. What gates is evidence.

*Never stored authoritatively.* There is no readiness column, in this module or anywhere
else: it is a pure function of the evidence counts and the configured thresholds,
computed at the moment of asking. That is what makes it impossible for a surface to
promise a feature the evidence no longer supports, and it is why the append-only metrics
row records the counts rather than the verdict.

*The bars are the single source.* :func:`bars` says what each state needs and where the
account stands against it; :func:`classify` is that same list read as pass or fail, and
the Profile screen is that same list rendered. A threshold cannot move for the engine
without moving on the screen, because there is only one of it.

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
from typing import Literal

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


Dimension = Literal["rated_films", "bands_spanned", "settled_share", "comparisons_per_film"]
"""What a bar measures. The last two are the two halves of the spec's explicit-comparison
share: how much of the library the owner settled, and how much they actually answered."""


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
    def settled_share(self) -> float:
        """How much of the library rests on judgments rather than on guesses.

        The "not dominated by provisional pairs" half of the ready bar.
        """
        return self.settled_films / self.rated_films if self.rated_films else 0.0

    @property
    def comparisons_per_film(self) -> float:
        """How much the owner has actually answered, per film they have rated.

        The "not dominated by implied pairs" half. Every rated film contributes implied
        pairs to the fit whether or not the owner ever judged it against anything, so
        what keeps those from swamping the real answers is the answers accumulating
        faster than the library does.
        """
        return self.explicit_comparisons / self.rated_films if self.rated_films else 0.0


@dataclass(frozen=True)
class Bar:
    """One threshold a state needs cleared, and where the account stands against it."""

    dimension: Dimension
    have: float
    need: float

    @property
    def cleared(self) -> bool:
        return self.have >= self.need


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


def bars(evidence: Evidence, settings: Settings) -> dict[Readiness, tuple[Bar, ...]]:
    """What each reachable state needs, and where this account stands against it.

    Cold is absent because every account is already at it; there is nothing to clear.

    Ready restates forming's band bar rather than inheriting it, so "why am I not ready
    yet" is answerable from the ready row alone - a big library with no band structure
    would otherwise show a row of full bars over a state it has not reached.
    """
    return {
        Readiness.forming: (
            Bar("rated_films", evidence.rated_films, settings.readiness_forming_films),
            Bar("bands_spanned", evidence.bands_spanned, settings.readiness_forming_bands),
        ),
        Readiness.ready: (
            Bar("rated_films", evidence.rated_films, settings.readiness_ready_films),
            Bar(
                "comparisons_per_film",
                evidence.comparisons_per_film,
                settings.readiness_ready_comparisons_per_film,
            ),
            Bar(
                "settled_share",
                evidence.settled_share,
                settings.readiness_ready_settled_share,
            ),
            Bar("bands_spanned", evidence.bands_spanned, settings.readiness_forming_bands),
        ),
    }


def classify(evidence: Evidence, settings: Settings) -> Readiness:
    """Which state the evidence supports. Derived on every read, stored nowhere."""
    reachable = bars(evidence, settings)
    if not _cleared(reachable[Readiness.forming]):
        return Readiness.cold
    if not _cleared(reachable[Readiness.ready]):
        return Readiness.forming
    return Readiness.ready


def _cleared(state: tuple[Bar, ...]) -> bool:
    return all(bar.cleared for bar in state)
