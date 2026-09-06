"""Readiness: cold, forming, or ready, read off the evidence every time it is asked for.

Three states gate the recommendation features, and there is no time component anywhere in
them - an account that rates fifty films in a weekend is as ready as one that took a
year, and one that has not opened the app since March is exactly as ready as it was in
March. What gates is evidence.

*Two dimensions, and only two.* Rated films and the bands they span (ADR 0013). There is
no comparison dimension: the ordering is complete the moment a film is rated, and
within-band order is read as a range rather than trusted as a verdict, so no count of
answers makes it more trustworthy than the band structure already is. This is what lets
a seed import of any real size cross both bars the instant matching completes.

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
looks at and leaves the thresholds to implementation, so they live in ``Settings`` where
an operator can move them without a migration.

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

from anchor.models import AccountFilm, LifecycleState, Placement
from anchor.settings import Settings


class Readiness(enum.StrEnum):
    """What the account's evidence currently supports."""

    cold = "cold"
    """Too little signal to train anything: no discovery, no ranked tier."""
    forming = "forming"
    """Enough rated films across enough bands for a stable fit. Discovery lights up."""
    ready = "ready"
    """A library big enough for "watch these next" to rest on something. The tier unlocks."""


Dimension = Literal["rated_films", "bands_spanned"]
"""What a bar measures: the two dimensions taste-profile.md names, and no others."""


@dataclass(frozen=True)
class Evidence:
    """The counts readiness is derived from, and the metrics row's context columns."""

    rated_films: int
    bands_spanned: int
    """Distinct half-star bands the account's placements sit in: the band structure."""


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
    spanned = await db.scalar(
        select(func.count(func.distinct(Placement.band))).where(Placement.account_id == account_id)
    )
    return Evidence(rated_films=rated or 0, bands_spanned=spanned or 0)


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
            Bar("bands_spanned", evidence.bands_spanned, settings.readiness_forming_bands),
        ),
    }


async def state(db: AsyncSession, account_id: uuid.UUID, settings: Settings) -> Readiness:
    """Where this account stands right now: the evidence counted and classified in one call."""
    return classify(await evidence(db, account_id), settings)


async def earned_spend(db: AsyncSession, account_id: uuid.UUID, settings: Settings) -> bool:
    """Whether this account has told Anchor enough to be worth spending money on.

    "Zero spend until an account reaches the *forming* bar" (taste-profile.md) said once,
    because it is asked in two places that cannot be one: the LLM seam gates every
    account-scoped call, and shared work has no account for the seam to read, so the
    caller that *causes* shared work gates it instead. Where the bar sits is a decision
    about spend rather than about either caller, and it belongs here with the bars.
    """
    return await state(db, account_id, settings) is not Readiness.cold


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
