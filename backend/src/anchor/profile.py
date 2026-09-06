"""The Profile screen's engine section: how ready the taste profile is, and what it says.

Two of the profile's three artifacts surface here. Readiness is the arithmetic, and the
whole point of showing it is honesty: an account that cannot yet be recommended to is
told so, in the terms that would change it, rather than being shown an empty ranked tier
and left to guess what went wrong (onboarding-and-import.md).

The prose profile is the other, and it is read, never made, on this path. The text is
whatever the last regeneration wrote, and its last-updated stamp rides along beside it -
one ambient line and nothing more (surfacing.md). Nothing here can trigger a
regeneration, ask whether one is running, or wait on one: the engine never narrates its
background work, and the module that would make the call is not even loaded in this
process (architecture.md).

So the payload is the state *and its arithmetic*: each bar the next state needs, what the
account has against it, and what it needs. The screen can then say "eleven more films"
instead of "not yet", and the number it says is the one the engine actually uses - the
bars come from :mod:`anchor.readiness`, so the screen cannot show a threshold the engine
is not gating on.

Nothing rating-shaped is here and nothing ever can be: readiness is counts, and ADR 0005
keeps scores off every surface anyway.
"""

from collections.abc import Sequence
from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel

from anchor import picker as picker_module
from anchor import prose as prose_module
from anchor import readiness as readiness_module
from anchor.accounts import CurrentAccount
from anchor.deps import AppSettings, DbSession
from anchor.models import CriteriaFrequency
from anchor.picker import Correction
from anchor.readiness import Bar, Dimension, Readiness

router = APIRouter(prefix="/api/profile")


class Evidence(BaseModel):
    """What the account has told Anchor, as readiness counts it: films, and bands.

    Two figures, because there are two dimensions (ADR 0013). The comparison counts that
    used to sit here are gone with the bar they served: the ordering is complete the
    moment a film is rated, so no count of answers makes it more trustworthy.
    """

    rated_films: int
    bands_spanned: int


class Threshold(BaseModel):
    """One bar a state needs cleared, and where the account stands against it."""

    dimension: Dimension
    have: float
    need: float

    @classmethod
    def of(cls, bar: Bar) -> "Threshold":
        return cls(dimension=bar.dimension, have=bar.have, need=bar.need)


class Progress(BaseModel):
    """How close the account is to one unlock: one line and one subtle bar.

    Ambient only, and the loudness ceiling for every pre-gate screen (surfacing.md). The
    thresholds are the engine's own bars, so a screen cannot promise a number the engine
    is not gating on; ``share`` is those bars averaged, which is the bar to draw.

    It lives here rather than on either screen because both pre-gate screens draw it -
    Watchlist against *ready*, Discovery against *forming* - and two copies of it would be
    two chances for the two screens to disagree about what progress means.
    """

    share: float
    thresholds: list[Threshold]

    @classmethod
    def toward(cls, bars: Sequence[Bar]) -> "Progress":
        cleared = [min(1.0, bar.have / bar.need) if bar.need else 1.0 for bar in bars]
        return cls(
            share=sum(cleared) / len(cleared) if cleared else 0.0,
            thresholds=[Threshold.of(bar) for bar in bars],
        )


class Stage(BaseModel):
    """A readiness state the account can reach, and exactly what stands between."""

    state: Readiness
    reached: bool
    thresholds: list[Threshold]


class Prose(BaseModel):
    """The owner-readable description of their taste, as the screen shows it.

    ``generated_at`` is what the ambient last-updated line renders and the only thing
    about the regeneration the owner is ever told. The trigger and the watermark stay in
    the row: what made Anchor rewrite this is the engine's business, and narrating it
    would be exactly the background chatter ADR 0011 rules out.
    """

    text: str
    version: int
    generated_at: datetime


class Profile(BaseModel):
    """The screen. ``stages`` omits cold, which every account is already at."""

    readiness: Readiness
    evidence: Evidence
    stages: list[Stage]
    criteria_frequency: CriteriaFrequency
    """How often the owner wants the bonus question after a placement, off included."""
    prose: Prose | None
    """None until the first regeneration lands; the section renders nothing rather than
    promising one is coming, because a cold account has not earned the spend."""
    corrections: list[Correction]
    """The claims the owner has thumbed down and not taken back.

    Beside the prose rather than on a screen of their own, because that is where they
    were made and where the owner would look for them. Correctable means visible: a
    correction the owner cannot find again is one they cannot undo.
    """


class CriteriaSetting(BaseModel):
    """The owner's choice of how often to be asked."""

    frequency: CriteriaFrequency


@router.get("")
async def profile(account: CurrentAccount, db: DbSession, settings: AppSettings) -> Profile:
    counted = await readiness_module.evidence(db, account.id)
    state = readiness_module.classify(counted, settings)
    reachable = readiness_module.bars(counted, settings)
    live = await prose_module.latest(db, account.id)
    order = list(Readiness)
    return Profile(
        readiness=state,
        evidence=Evidence(
            rated_films=counted.rated_films,
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
        criteria_frequency=account.criteria_frequency,
        prose=(
            None
            if live is None
            else Prose(text=live.text, version=live.version, generated_at=live.generated_at)
        ),
        corrections=await picker_module.corrections(db, account.id),
    )


@router.put("/criteria")
async def set_criteria_frequency(
    body: CriteriaSetting, account: CurrentAccount, db: DbSession
) -> CriteriaSetting:
    """Set how often the bonus question is offered, ``off`` included.

    A complete off switch, not a quieter setting: on ``off`` no card is offered and no
    offer is recorded, so nothing accumulates in the log while it is off and turning it
    back on does not surface a backlog of questions.
    """
    account.criteria_frequency = body.frequency
    await db.commit()
    return CriteriaSetting(frequency=account.criteria_frequency)
