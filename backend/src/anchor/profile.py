"""The Profile screen's engine section: how ready the taste profile is, and why.

Readiness is the only thing on this screen that exists yet - the prose profile, the
quality picker, and the Letterboxd area arrive with their own tickets - and the whole
point of showing it is honesty. An account that cannot yet be recommended to is told so,
in the terms that would change it, rather than being shown an empty ranked tier and left
to guess what went wrong (onboarding-and-import.md).

So the payload is the state *and its arithmetic*: each bar the next state needs, what the
account has against it, and what it needs. The screen can then say "eleven more films"
instead of "not yet", and the number it says is the one the engine actually uses.

Nothing rating-shaped is here and nothing ever can be: readiness is counts, and ADR 0005
keeps scores off every surface anyway.
"""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from anchor import readiness as readiness_module
from anchor.accounts import CurrentAccount
from anchor.deps import AppSettings, DbSession
from anchor.readiness import Readiness
from anchor.settings import Settings

router = APIRouter(prefix="/api/profile")

Dimension = Literal["rated_films", "explicit_share", "bands_spanned"]
"""The gating dimensions taste-profile.md names. The numbers behind them are tuning."""


class Evidence(BaseModel):
    """What the account has told Anchor, as readiness counts it."""

    rated_films: int
    explicit_comparisons: int
    settled_films: int
    """Rated films the owner's own comparisons settled, rather than a seed or an early bail."""
    explicit_share: float
    bands_spanned: int


class Threshold(BaseModel):
    """One bar a state needs cleared, and where the account stands against it."""

    dimension: Dimension
    have: float
    need: float

    @property
    def cleared(self) -> bool:
        return self.have >= self.need


class Stage(BaseModel):
    """A readiness state the account can reach, and exactly what stands between."""

    state: Readiness
    reached: bool
    thresholds: list[Threshold]


class Profile(BaseModel):
    """The screen. ``stages`` omits cold, which every account is already at."""

    readiness: Readiness
    evidence: Evidence
    stages: list[Stage]


@router.get("")
async def profile(account: CurrentAccount, db: DbSession, settings: AppSettings) -> Profile:
    evidence = await readiness_module.evidence(db, account.id)
    state = readiness_module.classify(evidence, settings)
    return Profile(
        readiness=state,
        evidence=Evidence(
            rated_films=evidence.rated_films,
            explicit_comparisons=evidence.explicit_comparisons,
            settled_films=evidence.settled_films,
            explicit_share=evidence.explicit_share,
            bands_spanned=evidence.bands_spanned,
        ),
        stages=[
            _stage(Readiness.forming, _forming(evidence, settings), state),
            _stage(Readiness.ready, _ready(evidence, settings), state),
        ],
    )


def _stage(state: Readiness, thresholds: list[Threshold], reached: Readiness) -> Stage:
    """A stage counts as reached once the account is at it or past it."""
    order = list(Readiness)
    return Stage(
        state=state,
        reached=order.index(reached) >= order.index(state),
        thresholds=thresholds,
    )


def _forming(evidence: readiness_module.Evidence, settings: Settings) -> list[Threshold]:
    return [
        Threshold(
            dimension="rated_films",
            have=evidence.rated_films,
            need=settings.readiness_forming_films,
        ),
        Threshold(
            dimension="bands_spanned",
            have=evidence.bands_spanned,
            need=settings.readiness_forming_bands,
        ),
    ]


def _ready(evidence: readiness_module.Evidence, settings: Settings) -> list[Threshold]:
    return [
        Threshold(
            dimension="rated_films",
            have=evidence.rated_films,
            need=settings.readiness_ready_films,
        ),
        Threshold(
            dimension="explicit_share",
            have=evidence.explicit_share,
            need=settings.readiness_ready_explicit_share,
        ),
        # Restated from forming: a big library with no band structure clears the other
        # two bars, and a row of full bars over "not yet" would explain nothing.
        Threshold(
            dimension="bands_spanned",
            have=evidence.bands_spanned,
            need=settings.readiness_forming_bands,
        ),
    ]
