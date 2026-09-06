"""The ranked tier: the engine's top thirty of the backlog, and the owner's overrides on it.

The tier is the one place in Anchor where the engine states an opinion about a film the
owner has not seen, and the whole design of this module is about making that opinion
*legible* rather than merely correct.

*Position is the entire statement.* Nothing rating-shaped is computed for display: a
score exists only inside this module, decides an order, and dies with the request (ADR
0005). A predicted band seen before watching would tilt the comparison answers
themselves, and that contamination is invisible to drift detection and permanent in the
ordering.

*The list is state, not a view.* Membership is persisted on the backlog account-films and
read back verbatim; nothing recomputes it at read time. That is what lets damping mean
anything, because a list derived on read would arrive at whatever the current fit happens
to say, every time, and the owner would be reading a different tier on every visit.

*Two speeds, deliberately.* The scored refresh - fresh scores, hysteresis, a swap budget,
cooldowns, staleness - runs at a session boundary. Everything the owner just did runs
immediately: a pin, a veto, a not-now, a film added to the backlog, a seat vacated by a
watch. The first is the engine changing its mind, which should roll in slowly; the second
is the app answering, which should not be queued behind anything (watchlist.md).

*The profile firewall.* Nothing here writes to the taste profile, and nothing may: pins,
vetoes, not-nows, rotations, and lingering are queue management, and only the ordering
trains taste (ADR 0004, ADR 0012).
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from anchor import readiness as readiness_module
from anchor import trainer, unlocks
from anchor.features import FeatureSpace
from anchor.models import (
    AccountFilm,
    Film,
    LifecycleState,
    TierState,
    TierZone,
    Unlock,
    WatchEvent,
    WatchStanding,
    WeightVector,
)
from anchor.readiness import Readiness
from anchor.settings import Settings

CAP = 30
"""How many films the tier holds. Spec, not tuning: watchlist.md fixes it."""

UP_NEXT = 5
"""The up-next zone, which is also the pin cap - a pin is a seat in that zone."""


# --- What the tier is computed from ---


@dataclass
class Candidate:
    """One backlog film as maintenance sees it: its bookkeeping, and this refresh's score.

    The score lives exactly this long. It is never stored, never returned, and never
    written anywhere a surface could reach it.
    """

    account_film: AccountFilm
    score: float

    @property
    def film_id(self) -> int:
        return self.account_film.film_id

    @property
    def seated(self) -> bool:
        return self.account_film.tier_zone is not None

    @property
    def pinned(self) -> bool:
        return self.account_film.pinned_at is not None

    @property
    def vetoed(self) -> bool:
        return self.account_film.vetoed_at is not None

    def cooling(self, watch_clock: int) -> bool:
        """Dropped or waved off too recently to come back: the no-bounce-backs rule."""
        mark = self.account_film.tier_reentry_watch
        return mark is not None and watch_clock < mark

    def protected(self, watch_clock: int, settings: Settings) -> bool:
        """Too newly seated for the engine to drop: the no-immediate-drops rule."""
        entered = self.account_film.tier_entered_watch
        return entered is not None and watch_clock - entered < settings.tier_enter_cooldown

    def passed_over(self, watch_clock: int) -> int:
        """Watches this film survived without being picked.

        Read off the seat rather than counted, because picking a tier film means watching
        it and a watched film has left the backlog: the watches since it sat down and the
        watches it was passed over are the same number by construction.
        """
        entered = self.account_film.tier_entered_watch
        return watch_clock - entered if entered is not None else 0


# --- The two entry points ---


async def refresh(db: AsyncSession, account_id: uuid.UUID, settings: Settings) -> None:
    """Maintenance as a session boundary runs it: fresh scores, damped by every mechanism.

    A session boundary is a moment rather than a record, and at this API the moment that
    exists is the owner arriving at the Watchlist - the next app open, or the first look
    after a rating session. So this runs on the read, gated on the fingerprint of what
    the tier is computed from: the fit it was scored with, and the watch clock its
    cooldowns were measured against.

    The gate is what makes running it on a read safe. Re-reading the screen - a different
    sort, a filter, a second tab - finds nothing to do and leaves the list alone, so the
    swap budget is spent once per real change rather than once per request, and what the
    owner is looking at never moves under their cursor.

    Deliberately not run from the retrain job, which would be the other reading of "end
    of a rating session": a retrain is queued per landed placement, so the budget would
    be spent per film rated and a shift meant to roll in over days would arrive in one
    evening of rating.
    """
    state = await _state(db, account_id)
    clock = await watch_clock(db, account_id)
    vector = await _vector(db, account_id)
    trained_at = vector.trained_at if vector is not None else None
    unchanged = state.refreshed_trained_at == trained_at and state.refreshed_watch_clock == clock
    if unchanged and not state.due:
        return
    state.due = False
    state.refreshed_trained_at = trained_at
    state.refreshed_watch_clock = clock
    await _maintain(
        db,
        account_id,
        settings,
        clock=clock,
        vector=vector,
        budget=settings.tier_swap_budget,
        rotating=True,
    )


async def reconcile(
    db: AsyncSession, account_id: uuid.UUID, settings: Settings, *, admit: int | None = None
) -> None:
    """Maintenance as the owner's own action runs it: immediate, and never a churn budget.

    A seat vacated by a watch, a veto, or a removal is refilled at once, because refilling
    a seat is not churn; a film the owner just added enters at once if it scores in,
    because the owner told the app something and reacting is the point. What this never
    does is roll the engine's own second thoughts in early: the swap budget is zero here,
    so the only displacement possible is the one the owner's action asked for.
    """
    await _maintain(
        db,
        account_id,
        settings,
        clock=await watch_clock(db, account_id),
        vector=await _vector(db, account_id),
        budget=0,
        rotating=False,
        admit=admit,
    )


async def _maintain(
    db: AsyncSession,
    account_id: uuid.UUID,
    settings: Settings,
    *,
    clock: int,
    vector: WeightVector | None,
    budget: int,
    rotating: bool,
    admit: int | None = None,
) -> None:
    """Bring the one persisted tier into line with what the rules now say it should be."""
    await _clear_departed(db, account_id)
    if await readiness_module.state(db, account_id, settings) is not Readiness.ready:
        await _clear_all(db, account_id)
        return
    candidates = await _candidates(db, account_id, vector)
    _seat(
        candidates,
        clock=clock,
        budget=budget,
        rotating=rotating,
        admit=admit,
        settings=settings,
    )


# --- The rules ---


def _seat(
    candidates: Sequence[Candidate],
    *,
    clock: int,
    budget: int,
    rotating: bool,
    admit: int | None,
    settings: Settings,
) -> None:
    """Decide who holds a seat, and write the decision onto the account-films.

    The order of the steps is the policy. Pins are settled first and are never revisited,
    because they are immune to all automatic maintenance. Then seats are *lost* - to a
    veto, to staleness - before any are won, so a rotation leaves a vacancy the same
    refresh can fill. Vacancies are filled free, since a seat standing empty is not
    damping anything. Only then does the engine displace anybody, and that is the one
    step the swap budget and the hysteresis margin govern.
    """
    pins = sorted(
        (candidate for candidate in candidates if candidate.pinned),
        key=lambda candidate: (candidate.account_film.pinned_at or datetime.min, candidate.film_id),
    )[:UP_NEXT]
    pinned_ids = {candidate.film_id for candidate in pins}
    capacity = CAP - len(pins)

    held: list[Candidate] = []
    for candidate in candidates:
        if candidate.film_id in pinned_ids or not candidate.seated:
            continue
        stale = rotating and candidate.passed_over(clock) >= settings.tier_staleness_watches
        if candidate.vetoed:
            _unseat(candidate)  # a veto is answered now and lifted the same way
        elif stale:
            _unseat(candidate, reentry=clock + settings.tier_reentry_cooldown)
        else:
            held.append(candidate)

    holding = {candidate.film_id for candidate in held}
    challengers = sorted(
        (
            candidate
            for candidate in candidates
            if candidate.film_id not in pinned_ids
            and candidate.film_id not in holding
            and not candidate.vetoed
            and not candidate.cooling(clock)
        ),
        key=lambda candidate: (-candidate.score, candidate.film_id),
    )
    held.sort(key=lambda candidate: (-candidate.score, candidate.film_id))
    # A pin takes its seat from the cap, not from the zone, so pinning a film the tier did
    # not already hold leaves one incumbent too many. The weakest gives way, and without a
    # re-entry cooldown: it was crowded out by the owner rather than dropped by the engine,
    # and unpinning should be able to hand the seat straight back.
    for surplus in held[capacity:]:
        _unseat(surplus)
    del held[capacity:]

    admitted: list[Candidate] = []
    while challengers and len(held) + len(admitted) < capacity:
        admitted.append(challengers.pop(0))

    margin = settings.tier_hysteresis * _spread(candidates)
    # The newly-backlogged exception is to the budget alone, so it runs as one extra swap
    # rather than as a rule of its own: hysteresis and the enter cooldown still bind.
    _displace(
        held, challengers, admitted, budget=budget, margin=margin, clock=clock, settings=settings
    )
    if admit is not None:
        arrival = [candidate for candidate in challengers if candidate.film_id == admit]
        _displace(held, arrival, admitted, budget=1, margin=margin, clock=clock, settings=settings)

    for candidate in admitted:
        candidate.account_film.tier_entered_watch = clock
    _place(pins, sorted(held + admitted, key=lambda one: (-one.score, one.film_id)))


def _displace(
    held: list[Candidate],
    challengers: list[Candidate],
    admitted: list[Candidate],
    *,
    budget: int,
    margin: float,
    clock: int,
    settings: Settings,
) -> None:
    """Swap the weakest droppable incumbent out for a challenger that clearly beats it.

    Clearly is the hysteresis margin: a score wobble that reorders two adjacent films must
    not cost anybody their seat, or the tier would churn on noise the owner cannot see the
    cause of. The loop stops at the first challenger that fails the margin, since the
    challengers are sorted and no later one can pass it either.

    The test is on the *difference*, never ``incumbent + margin``: the margin is a share
    of the backlog's spread, and a difference of two scores can never round above the
    spread they both sit inside, whereas ``low + (high - low)`` can round below ``high``
    by an ulp. A margin as wide as the spread then means what it says - nothing moves -
    instead of depending on the last bit of a fit that differs between machines.
    """
    swaps = 0
    while challengers and swaps < budget:
        droppable = [one for one in held if not one.protected(clock, settings)]
        if not droppable:
            return
        incumbent = min(droppable, key=lambda one: (one.score, -one.film_id))
        challenger = challengers[0]
        if challenger.score - incumbent.score <= margin:
            return
        held.remove(incumbent)
        _unseat(incumbent, reentry=clock + settings.tier_reentry_cooldown)
        admitted.append(challengers.pop(0))
        swaps += 1


def _place(pins: Sequence[Candidate], engine: Sequence[Candidate]) -> None:
    """Write the final order: pins first in pin order, then the engine's picks by score.

    The up-next zone is the first five of that, whoever they are - a pinned film sits
    above the engine's picks by construction rather than by a rule of its own.
    """
    for position, candidate in enumerate([*pins, *engine]):
        candidate.account_film.tier_zone = TierZone.up_next if position < UP_NEXT else TierZone.pool
        candidate.account_film.tier_position = position


def _unseat(candidate: Candidate, *, reentry: int | None = None) -> None:
    account_film = candidate.account_film
    account_film.tier_zone = None
    account_film.tier_position = None
    account_film.tier_entered_watch = None
    if reentry is not None:
        account_film.tier_reentry_watch = reentry


def _spread(candidates: Sequence[Candidate]) -> float:
    """How far apart the backlog's scores run, which is the scale the margin is read in."""
    scores = [candidate.score for candidate in candidates]
    return max(scores) - min(scores) if scores else 0.0


# --- The owner's overrides ---


async def _applied(db: AsyncSession, account_film: AccountFilm, settings: Settings) -> None:
    """Every override lands the same way: at once, and with the engine told to look again.

    The immediate half is a reconcile with no swap budget, so the only displacement is
    the one the owner asked for. The ``due`` flag is the rest of the list's turn, at the
    next boundary: an override changes who is *eligible* without moving the fit or the
    clock - a lifted veto is a film handed back - so the fingerprint alone would report
    nothing to do and the film would sit out until the owner happened to rate something.
    """
    (await _state(db, account_film.account_id)).due = True
    await db.flush()
    await reconcile(db, account_film.account_id, settings)


async def pin(db: AsyncSession, account_film: AccountFilm, settings: Settings) -> None:
    """Hold this film in the up-next zone until the owner watches it or takes it back.

    Pinning lifts a veto rather than colliding with one. The two are the same kind of
    statement about the queue and the owner has just made the newer, more specific one;
    refusing would be the app arguing with them about a preference it holds for them.
    """
    account_film.pinned_at = datetime.now(UTC)
    account_film.vetoed_at = None
    account_film.tier_reentry_watch = None
    await _applied(db, account_film, settings)


async def unpin(db: AsyncSession, account_film: AccountFilm, settings: Settings) -> None:
    """Give the seat back to the engine, which may well keep the film in it.

    The seat is re-dated on the way out of the pin: the film is arriving as an engine pick
    now, and its staleness should be counted from the moment it became one rather than
    from a pin the owner may have held for months.
    """
    account_film.pinned_at = None
    account_film.tier_entered_watch = await watch_clock(db, account_film.account_id)
    await _applied(db, account_film, settings)


async def veto(db: AsyncSession, account_film: AccountFilm, settings: Settings) -> None:
    """Bar this film from the tier until the owner lifts it. Never distaste, and never a score.

    The film stays in the backlog and its score is untouched, so lifting the veto returns
    it to exactly the standing it would have had - which is what makes "saving it for an
    occasion" a thing the owner can say here without it meaning anything else.
    """
    account_film.vetoed_at = datetime.now(UTC)
    account_film.pinned_at = None
    await _applied(db, account_film, settings)


async def lift(db: AsyncSession, account_film: AccountFilm, settings: Settings) -> None:
    """Take the bar off. The film is an ordinary candidate again, from this moment."""
    account_film.vetoed_at = None
    await _applied(db, account_film, settings)


async def not_now(db: AsyncSession, account_film: AccountFilm, settings: Settings) -> None:
    """Rotate this film out with the standard cooldown: the mood-level version of a veto."""
    clock = await watch_clock(db, account_film.account_id)
    account_film.tier_zone = None
    account_film.tier_position = None
    account_film.tier_entered_watch = None
    account_film.tier_reentry_watch = clock + settings.tier_reentry_cooldown
    account_film.pinned_at = None
    await _applied(db, account_film, settings)


# --- The unlock ---


async def note_unlock(db: AsyncSession, account_id: uuid.UUID, settings: Settings) -> None:
    """Arm whatever readiness bars this account has crossed, and react to the tier's own.

    The dots themselves belong to :mod:`anchor.unlocks`, which every surface that could
    be the first to notice calls. What is this module's business is the side effect: the
    crossing is itself a change to what the tier is computed from, and the only one there
    may be, since the fit and the clock can both stand where the last pre-gate read
    stamped them with the retrain still queued. Without this the one announced moment
    opens on an empty screen.
    """
    if Unlock.watchlist in await unlocks.arm(db, account_id, settings):
        state = await _state(db, account_id)
        state.due = True
        await db.flush()


# --- Reads ---


async def watch_clock(db: AsyncSession, account_id: uuid.UUID) -> int:
    """The account's watch clock: the count of its watch events, imported ones included.

    Every cooldown and staleness measure here is denominated in this rather than in
    calendar time, so an account nobody is using never changes behind its owner's back.
    """
    return (
        await db.scalar(
            select(func.count()).select_from(WatchEvent).where(WatchEvent.account_id == account_id)
        )
    ) or 0


def standing(account_film: AccountFilm) -> WatchStanding:
    """Where a film stood on the watchlist when a watch was logged for it.

    Capture-or-lose-forever (evaluation.md): tier membership churns and keeps no history,
    so a watch that does not record the standing at the moment it happened records it
    never. A pinned film counts as the owner's pick, never the engine's.
    """
    if account_film.pinned_at is not None:
        return WatchStanding.pinned
    if account_film.tier_zone is TierZone.up_next:
        return WatchStanding.up_next
    if account_film.tier_zone is TierZone.pool:
        return WatchStanding.pool
    return WatchStanding.plain_backlog


async def _state(db: AsyncSession, account_id: uuid.UUID) -> TierState:
    """This account's tier state, created on first sight so callers never handle its absence."""
    state = await db.scalar(select(TierState).where(TierState.account_id == account_id))
    if state is not None:
        return state
    await db.execute(
        insert(TierState)
        .values(id=uuid.uuid4(), account_id=account_id)
        .on_conflict_do_nothing(index_elements=[TierState.account_id])
    )
    await db.flush()
    fetched = await db.scalar(select(TierState).where(TierState.account_id == account_id))
    assert fetched is not None  # just inserted, or inserted by a request racing this one
    return fetched


async def _vector(db: AsyncSession, account_id: uuid.UUID) -> WeightVector | None:
    vector: WeightVector | None = await db.scalar(
        select(WeightVector).where(WeightVector.account_id == account_id)
    )
    return vector


async def _candidates(
    db: AsyncSession, account_id: uuid.UUID, vector: WeightVector | None
) -> list[Candidate]:
    """Every backlog film, scored. The tier draws only from here, by construction."""
    rows = list(
        await db.execute(
            select(AccountFilm, Film)
            .join(Film, Film.tmdb_id == AccountFilm.film_id)
            .where(
                AccountFilm.account_id == account_id,
                AccountFilm.state == LifecycleState.backlog,
            )
        )
    )
    if vector is None:
        # No fit yet, so nothing is better than anything: every film scores the same and
        # the order falls back on the tie-break, which is stable but says nothing.
        return [Candidate(account_film, 0.0) for account_film, _ in rows]
    space = FeatureSpace.from_json(vector.space)
    weights = np.array([vector.weights.get(column, 0.0) for column in space.columns])
    return [
        Candidate(account_film, trainer.score(weights, space, film)) for account_film, film in rows
    ]


async def _clear_departed(db: AsyncSession, account_id: uuid.UUID) -> None:
    """Wipe the bookkeeping of any film that has left the backlog.

    A watched film's seat, pin, and veto all stop meaning anything at once, and the seat
    it vacated is a vacancy the same maintenance pass fills.
    """
    await db.execute(
        update(AccountFilm)
        .where(
            AccountFilm.account_id == account_id,
            AccountFilm.state != LifecycleState.backlog,
            AccountFilm.tier_zone.is_not(None)
            | AccountFilm.pinned_at.is_not(None)
            | AccountFilm.vetoed_at.is_not(None),
        )
        .values(_EMPTY)
    )


async def _clear_all(db: AsyncSession, account_id: uuid.UUID) -> None:
    """No tier at all below ready: the pre-gate screen is the honestly-unranked backlog."""
    await db.execute(
        update(AccountFilm)
        .where(AccountFilm.account_id == account_id, AccountFilm.tier_zone.is_not(None))
        .values(tier_zone=None, tier_position=None, tier_entered_watch=None)
    )


_EMPTY = {
    "tier_zone": None,
    "tier_position": None,
    "tier_entered_watch": None,
    "tier_reentry_watch": None,
    "pinned_at": None,
    "vetoed_at": None,
}
