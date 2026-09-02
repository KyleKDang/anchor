"""The Profile screen's engine section: how ready the taste profile is, and why.

Readiness is the only thing on this screen that exists yet - the prose profile, the
quality picker, and the Letterboxd area arrive with their own tickets - and the whole
point of showing it is honesty. An account that cannot yet be recommended to is told so,
in the terms that would change it, rather than being shown an empty ranked tier and left
to guess what went wrong (onboarding-and-import.md).

So the payload is the state *and its arithmetic*: each bar the next state needs, what the
account has against it, and what it needs. The screen can then say "eleven more films"
instead of "not yet", and the number it says is the one the engine actually uses - the
bars come from :mod:`anchor.readiness`, so the screen cannot show a threshold the engine
is not gating on.

Nothing rating-shaped is here and nothing ever can be: readiness is counts, and ADR 0005
keeps scores off every surface anyway.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from anchor import readiness as readiness_module
from anchor.accounts import CurrentAccount
from anchor.deps import AppSettings, DbSession
from anchor.readiness import Bar, Dimension, Readiness

router = APIRouter(prefix="/api/profile")


class Evidence(BaseModel):
    """What the account has told Anchor, as readiness counts it."""

    rated_films: int
    explicit_comparisons: int
    settled_films: int
    """Rated films the owner's own comparisons settled, rather than a seed or an early bail."""
    settled_share: float
    comparisons_per_film: float
    bands_spanned: int


class Threshold(BaseModel):
    """One bar a state needs cleared, and where the account stands against it."""

    dimension: Dimension
    have: float
    need: float

    @classmethod
    def of(cls, bar: Bar) -> "Threshold":
        return cls(dimension=bar.dimension, have=bar.have, need=bar.need)


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
    counted = await readiness_module.evidence(db, account.id)
    state = readiness_module.classify(counted, settings)
    reachable = readiness_module.bars(counted, settings)
    order = list(Readiness)
    return Profile(
        readiness=state,
        evidence=Evidence(
            rated_films=counted.rated_films,
            explicit_comparisons=counted.explicit_comparisons,
            settled_films=counted.settled_films,
            settled_share=counted.settled_share,
            comparisons_per_film=counted.comparisons_per_film,
            bands_spanned=counted.bands_spanned,
        ),
        stages=[
            Stage(
                state=reachable_state,
                # A stage counts as reached once the account is at it or past it.
                reached=order.index(state) >= order.index(reachable_state),
                thresholds=[Threshold.of(bar) for bar in state_bars],
            )
            for reachable_state, state_bars in reachable.items()
        ],
    )
