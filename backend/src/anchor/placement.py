"""The band picker: the whole of rating a film.

The picker shows the ten bands, each with its anchor pool, and the owner picks one. That
is the rating - no questions asked, because with the pools on screen the pick has already
been made against the owner's own references, and a check after every rating would be the
chore this design removed (ADR 0013).

The film lands in its band at the rank the default order gives it, and the done screen
says where that is. Correcting it is the wall's job, not a step here: the primary way on
from the done screen is "adjust on the wall", and there is no undo button because the
wall *is* the undo.

*The picker holds no state.* A rating in progress needs no entity (data-model.md): the
band the owner taps is the whole of the input, so opening the picker is a read and
abandoning it is walking away. The film was already watched-unrated and already seated in
the rate-later queue before the picker opened, which is what makes walking away safe
without the client having to signal that it happened.

*Re-rating is the same picker.* Opened from a film's own page or from a rewatch, with the
current band marked. Landing in the same band keeps the rank, landing in another takes
the default rank there and retires the anchor mark (rating-system.md).

*A range is narrowed by the screen carrying the answers.* An owner unsure between two or
three adjacent bands selects them, and comparisons narrow the range down. None of that is
stored while it runs: the screen holds the answers it has given and hands them back, and
:mod:`anchor.narrowing` replays them to the same question every time. So the picker still
holds no state, abandoning is still walking away, and the next attempt starts fresh
however far the last one got - while the answers themselves are in the log from the
moment they are given, because each one is a judgment the owner made.

The screen never names its opponent, only its answer. Which film each answer was about is
re-derived here from the range and the answers before it, so a run of answers cannot ask
for a film the rule would not have offered, and one that could not have happened at all
is refused rather than half-read.
"""

import uuid
from dataclasses import dataclass

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from anchor import anchors as anchors_module
from anchor import criteria, jobs
from anchor import narrowing as narrowing_module
from anchor import ordering as ordering_module
from anchor import tier as tier_module
from anchor.accounts import CurrentAccount
from anchor.catalog import FilmCard
from anchor.criteria import CriteriaCard
from anchor.deps import AppJobs, AppSettings, DbSession
from anchor.errors import ApiError
from anchor.films import Neighbours
from anchor.models import (
    BANDS,
    Account,
    AccountFilm,
    ComparisonContext,
    ComparisonKind,
    ComparisonLogEntry,
    ComparisonVerdict,
    Film,
    LifecycleState,
    Placement,
    Unlock,
)
from anchor.narrowing import Answer, Narrowing
from anchor.settings import Settings

AS_LOGGED = {
    # ``film_a`` is always the film being rated and ``film_b`` the one it was set
    # against, so the verdict reads directly: ``a`` means the subject won (taste.py).
    Answer.better: ComparisonVerdict.a,
    Answer.worse: ComparisonVerdict.b,
    Answer.same: ComparisonVerdict.tied,
    Answer.skip: ComparisonVerdict.skip,
}
"""How the picker's four answers are written down. A skip is recorded saying nothing,
which is the honest record of the owner declining to judge."""

router = APIRouter(prefix="/api/placements")

POOL_SHOWN = 5
"""Anchors shown per band row. A handful of posters, with a count where the pool is
larger (screens-and-flows.md): the row is a reminder of the owner's references, not a
list of them."""

ANSWER_CEILING = 500
"""How many answers one narrowing may hand back. A ceiling on the request, not the owner.

Nothing in rating-system.md bounds a narrowing: skipping swaps in the band's next
candidate, and a band may hold any number of anchors, so the honest limit is "one answer
per film the range can offer". This is far past any band a person keeps in one place, and
it is here only so a single request cannot hand the replay an unbounded list."""


# --- Wire shapes ---


class BandRow(BaseModel):
    """One band of the picker: its value, and the references the owner picks against."""

    band: float
    pool: list[FilmCard]
    """Up to :data:`POOL_SHOWN` of the band's anchors, most recently marked first."""
    pool_total: int
    """The whole pool's size, so a row can say how many more it stands for."""


class Picker(BaseModel):
    """The picker screen: the film being rated, and the ten bands to put it in."""

    film: FilmCard
    bands: list[BandRow]
    current_band: float | None
    """The film's band on a re-rate, marked on the row; None when it is not rated yet."""
    current_rank: int | None
    """Where it currently sits in that band, shown beside the mark on a re-rate."""


class Landed(BaseModel):
    """The done screen: where the film landed, and the two ways on from it."""

    film: FilmCard
    band: float
    rank: int
    band_size: int
    """How many films the band holds, so the rank reads as "3 of 41"."""
    anchor: bool
    """The film carries an anchor mark, which a cross-band re-rate has just retired."""
    neighbours: Neighbours
    unlocked: list[Unlock]
    """What this very landing unlocked, and empty on every other one.

    One line, once ever, on the screen of the act that earned it (surfacing.md); the only
    other marker an unlock gets is the nav's one-time dot.
    """
    anchor_nudge: bool
    """The account has no anchors at all: the one line saying what marking one does."""
    criteria: CriteriaCard | None = None
    """The optional bonus question this landing earned, and usually None (taste-profile.md).

    It rides on the done screen rather than arriving from a call of its own because it is
    a bonus: a screen that had to fetch it could be left waiting on something the owner
    never asked for. Answering it is optional and ignoring it costs nothing.
    """


class Opponent(BaseModel):
    """The film a comparison sets the subject against, and the band it stands for."""

    film: FilmCard
    band: float
    """What the question is about: the band, never this film's own worth."""
    anchor: bool
    """An anchor of that band, or the stand-in a band with no anchor left is shown by."""


class Boundary(BaseModel):
    """The boundary question: the two seam films, and which the owner says it is closer to."""

    upper: FilmCard
    upper_band: float
    lower: FilmCard
    lower_band: float


class Step(BaseModel):
    """Where a narrowing stands, and the one thing the screen does about it.

    Exactly one of the four is set. ``question`` and ``boundary`` are questions to ask,
    ``choose`` hands the owner what is left because nothing can be asked of it, and
    ``band`` is the answer: the range narrowed to one, ready to land.
    """

    bands: list[float]
    """The bands still in the range, best first."""
    question: Opponent | None = None
    boundary: Boundary | None = None
    choose: bool = False
    band: float | None = None


class Narrow(BaseModel):
    """A step of narrowing: the range, the answers already given, and the newest one.

    ``answered`` is what the screen is carrying, and ``verdict`` the answer the
    owner has just given - the one, and the only one, this call writes to the log. A call
    with no verdict asks the range where it stands and writes nothing, which is how the
    first question is fetched and how a reloaded screen finds its place again.
    """

    bands: list[float] = Field(min_length=2, max_length=3)
    answered: list[Answer] = Field(default_factory=list, max_length=ANSWER_CEILING)
    verdict: Answer | None = None


class Pick(BaseModel):
    """The owner's answer: one of the ten bands, and what got them to it.

    An outright pick names a band and nothing else. A pick that ends a range carries the
    range and the answers given, because the landing is clipped to what the comparisons
    proved and the log entry says what was being narrowed. A boundary answer names the
    exemplar the film was judged closer to, and the band follows from it.
    """

    band: float
    bands: list[float] = Field(default_factory=list, max_length=3)
    answered: list[Answer] = Field(default_factory=list, max_length=ANSWER_CEILING)
    closer: int | None = None


# --- The flow ---


@router.get("/{tmdb_id}")
async def picker(tmdb_id: int, account: CurrentAccount, db: DbSession) -> Picker:
    """Open the band picker on a watched film, or on a rated one to re-rate it.

    A pure read: the picker keeps no state, so this is safe to call again at any time and
    the screen does exactly that on every mount.
    """
    await _rateable(db, account, tmdb_id)
    film = await _film(db, tmdb_id)
    pools = await anchors_module.pools(db, account.id)
    cards = await ordering_module.cards(
        db, [film_id for pool in pools.values() for film_id in pool[:POOL_SHOWN]]
    )
    placement = await ordering_module.placement_of(db, account.id, tmdb_id)
    return Picker(
        film=FilmCard.of(film),
        bands=[
            BandRow(
                band=band,
                pool=[
                    cards[film_id]
                    for film_id in pools.get(band, ())[:POOL_SHOWN]
                    if film_id in cards
                ],
                pool_total=len(pools.get(band, ())),
            )
            for band in BANDS
        ],
        current_band=placement.band if placement else None,
        current_rank=placement.rank if placement else None,
    )


@router.post("/{tmdb_id}/narrow")
async def narrow(
    tmdb_id: int,
    body: Narrow,
    account: CurrentAccount,
    db: DbSession,
    settings: AppSettings,
) -> Step:
    """Where a range stands, and the answer that got it there.

    The owner unsure between two or three adjacent bands selects them and this is every
    step of the narrowing that follows: hand back the answers so far, get the next
    question. The answer just given is appended to the log before the next question is
    worked out, so walking away from the screen at any point leaves every judgment
    already made recorded and nothing else behind.
    """
    await _rateable(db, account, tmdb_id)
    selected = _selected(body.bands)
    ordering = await ordering_module.load(db, account.id)
    seed = narrowing_module.seed_for(account.id, tmdb_id, settings.picker_seed)
    if body.verdict is not None:
        # The answer answers whatever question the ones before it lead to, and the rule is
        # what says which film that was. A run of answers that leads somewhere with
        # nothing to ask is a screen out of step with itself, not a judgment.
        pending = _replay(ordering, tmdb_id, selected, body.answered, seed)
        if pending.question is None:
            raise ApiError(409, "nothing_asked", "There is no question waiting for an answer.")
        db.add(
            ComparisonLogEntry(
                account_id=account.id,
                kind=ComparisonKind.band_comparison,
                subject_film_id=tmdb_id,
                film_a_id=tmdb_id,
                film_b_id=pending.question.film_id,
                verdict=AS_LOGGED[body.verdict],
                band=None,
                range_top=pending.bands[0],
                range_bottom=pending.bands[-1],
                context=_moment(await ordering_module.placement_of(db, account.id, tmdb_id)),
            )
        )
    answers = [*body.answered, *([body.verdict] if body.verdict is not None else [])]
    step = await _step(db, _replay(ordering, tmdb_id, selected, answers, seed))
    await db.commit()
    return step


@router.post("/{tmdb_id}/band")
async def pick(
    tmdb_id: int,
    body: Pick,
    account: CurrentAccount,
    db: DbSession,
    queue: AppJobs,
    settings: AppSettings,
) -> Landed:
    """Rate the film: the band the owner settled on, at the rank the landing rule gives it.

    This is the one write that seats a film. It appends the band pick to the log, seats
    the film, arms whatever readiness bar it just crossed, and hands back the done screen.

    A landing never contradicts an answer just given (rating-system.md), so where the
    band came out of a range the rank is clipped to those answers: above every film the
    owner said it beat, below every film they said it lost to, and beside the seam film
    they judged it closer to. An outright pick has nothing to clip against and takes the
    rank the default order gives it, which is every rating before ranges existed.
    """
    if body.band not in BANDS:
        raise ApiError(422, "not_a_band", "A rating is one of the ten half-star values.")
    account_film = await _rateable(db, account, tmdb_id)
    film = await _film(db, tmdb_id)
    order = ordering_module.default_order(settings)
    placement = await ordering_module.placement_of(db, account.id, tmdb_id)
    context = _moment(placement)
    ordering = await ordering_module.load(db, account.id)
    landing = _landing(tmdb_id, body, ordering, settings, account.id)

    db.add(
        ComparisonLogEntry(
            account_id=account.id,
            kind=ComparisonKind.band_pick,
            subject_film_id=tmdb_id,
            film_a_id=tmdb_id,
            film_b_id=None,
            verdict=None,
            band=landing.band,
            # The range this pick was narrowing, which is what tells the three kinds of
            # pick apart (data-model.md). No range at all is an outright pick; two
            # exemplars is the boundary question; a range still holding more than one
            # band is the last resort, the owner picking because nothing could be asked;
            # and a range down to one band is a pick the comparisons themselves settled.
            range_top=landing.narrowed[0] if landing.narrowed else None,
            range_bottom=landing.narrowed[-1] if landing.narrowed else None,
            exemplar_upper_id=landing.seam.upper if landing.seam else None,
            exemplar_lower_id=landing.seam.lower if landing.seam else None,
            context=context,
        )
    )

    async def seated() -> int:
        """The rank before the clip: fixed by the boundary question, or the default order."""
        return landing.rank or await ordering_module.default_rank(
            db, account.id, landing.band, film, order
        )

    if placement is None:
        rank = _clipped(ordering, tmdb_id, landing, await seated())
        await ordering_module.land(db, account_film, band=landing.band, rank=rank)
    elif landing.band == placement.band:
        # The re-rate that keeps its rank: the owner re-affirmed the rating and where
        # they had put the film inside it was never the question - unless they have just
        # answered comparisons that say otherwise, which the clip is what honours. The
        # re-rate call only restarts the "recently rated" clock here; the move, where the
        # answers ask for one, is the shift.
        rank = _clipped(ordering, tmdb_id, landing, landing.rank or placement.rank)
        await ordering_module.re_rate(db, placement, band=landing.band, rank=rank)
        await ordering_module.shift(db, placement, rank=rank)
    else:
        rank = _clipped(ordering, tmdb_id, landing, await seated())
        await ordering_module.re_rate(db, placement, band=landing.band, rank=rank)

    await jobs.schedule_retrain(db, queue, account.id)
    # Through the tier rather than straight at the dots: arming the watchlist's one has a
    # consequence the tier owns, and a rating is exactly the act that can cross the bar
    # without moving anything else the tier is computed from.
    crossed = await tier_module.note_unlock(db, account.id, settings)
    card = await criteria.offer(db, account, tmdb_id, context)
    await criteria.buy_tags(db, queue, account.id, tmdb_id, settings)
    landed = await _landed(db, account, tmdb_id, unlocked=crossed, card=card)
    await db.commit()
    return landed


async def _landed(
    db: AsyncSession,
    account: Account,
    tmdb_id: int,
    *,
    unlocked: set[Unlock],
    card: CriteriaCard | None,
) -> Landed:
    """The done screen, read back off the ordering the pick just wrote."""
    ordering = await ordering_module.load(db, account.id)
    standing = ordering.standing(tmdb_id)
    assert standing is not None  # the film was rated inside this request
    cards = await ordering_module.cards(db, [tmdb_id, *standing.named()])
    return Landed(
        film=cards[tmdb_id],
        band=standing.band,
        rank=standing.rank,
        band_size=standing.band_size,
        anchor=standing.anchored,
        neighbours=Neighbours(
            above=cards.get(standing.above) if standing.above else None,
            below=cards.get(standing.below) if standing.below else None,
        ),
        unlocked=[unlock for unlock in Unlock if unlock in unlocked],
        anchor_nudge=not await anchors_module.counts(db, account.id),
        criteria=card,
    )


# --- The range ---


@dataclass(frozen=True)
class Landing:
    """Where a pick puts the film, and what the owner's answers say about it."""

    band: float
    rank: int | None
    """Fixed by the boundary question, or None to let the default order seat it."""
    seam: narrowing_module.Seam | None
    """The boundary question this pick answered, and None for every other pick."""
    narrowing: Narrowing | None
    """The answers to clip against, and None where the pick narrowed nothing."""

    @property
    def narrowed(self) -> tuple[float, ...]:
        """The range this pick was narrowing, as the log records it. Empty when outright."""
        return self.narrowing.bands if self.narrowing is not None else ()


def _selected(bands: list[float]) -> tuple[float, ...]:
    """The range the owner selected: two or three adjacent bands, best first.

    Adjacency is the whole meaning of a range - "I am unsure between these" is a claim
    about neighbours - so a selection that skips a band is not a range that any of the
    narrowing rules were written for, and one band on its own is not being unsure at all;
    that is the outright pick, which names no range.
    """
    selected = tuple(bands)
    start = BANDS.index(selected[0]) if selected and selected[0] in BANDS else None
    if (
        start is None
        or len(selected) not in (2, 3)
        or selected != BANDS[start : start + len(selected)]
    ):
        raise ApiError(422, "not_a_range", "A range is two or three adjacent bands.")
    return selected


def _replay(
    ordering: ordering_module.Ordering,
    tmdb_id: int,
    selected: tuple[float, ...],
    answers: list[narrowing_module.Answer],
    seed: int,
) -> Narrowing:
    """Where the rules put a range that has been answered this way, refusing a run they could not.

    Every answer the screen sends back has to correspond to a question the rules would
    have asked, or the two are out of step and the extra answers are about films nobody
    was ever offered. Refusing that keeps the replay total: what comes back is always a
    narrowing that could actually have happened.
    """
    narrowing = narrowing_module.narrow(ordering, tmdb_id, selected, answers, seed)
    if len(narrowing.answered) != len(answers):
        raise ApiError(409, "out_of_step", "Those answers do not match this range.")
    return narrowing


def _landing(
    tmdb_id: int,
    body: Pick,
    ordering: ordering_module.Ordering,
    settings: Settings,
    account_id: uuid.UUID,
) -> Landing:
    """What the owner's pick amounts to, checked against the narrowing that produced it.

    An outright pick is taken as given: with the pools on screen it was made against the
    owner's own references and there is nothing to check it against. A pick inside a range
    is checked against the range as the answers left it, because the screen carries the
    answers and a band the comparisons ruled out is not one the owner chose.

    A range sitting at the boundary question can only be answered by it. The screen offers
    the two seam films and nothing else there, and the boundary question is what settles a
    seam (CONTEXT.md) rather than one of several ways to; a band arriving instead would
    land the film without the answer that was supposed to place it.
    """
    if not body.bands:
        return Landing(band=body.band, rank=None, seam=None, narrowing=None)

    selected = _selected(body.bands)
    seed = narrowing_module.seed_for(account_id, tmdb_id, settings.picker_seed)
    narrowing = _replay(ordering, tmdb_id, selected, body.answered, seed)
    if narrowing.seam is not None:
        if body.closer not in (narrowing.seam.upper, narrowing.seam.lower):
            raise ApiError(409, "not_the_boundary", "That is not one of the boundary films.")
        band, rank = narrowing_module.beside(ordering, tmdb_id, narrowing.seam, body.closer)
        return Landing(band=band, rank=rank, seam=narrowing.seam, narrowing=narrowing)
    if body.closer is not None:
        raise ApiError(409, "not_the_boundary", "No boundary question is waiting.")
    if body.band not in narrowing.bands:
        raise ApiError(409, "outside_the_range", "Your answers have ruled that band out.")
    return Landing(band=body.band, rank=None, seam=None, narrowing=narrowing)


def _clipped(ordering: ordering_module.Ordering, tmdb_id: int, landing: Landing, rank: int) -> int:
    """The rank the film takes, moved only as far as the owner's own answers require."""
    if landing.narrowing is None:
        return rank
    return narrowing_module.landing_rank(ordering, tmdb_id, landing.band, rank, landing.narrowing)


async def _step(db: AsyncSession, narrowing: Narrowing) -> Step:
    """One step of a narrowing as the screen meets it, with the films it has to show."""
    named = [one.film_id for one in (narrowing.question,) if one is not None]
    if narrowing.seam is not None:
        named += [narrowing.seam.upper, narrowing.seam.lower]
    cards = await ordering_module.cards(db, named)
    return Step(
        bands=list(narrowing.bands),
        question=(
            Opponent(
                film=cards[narrowing.question.film_id],
                band=narrowing.question.band,
                anchor=narrowing.question.anchor,
            )
            if narrowing.question is not None
            else None
        ),
        boundary=(
            Boundary(
                upper=cards[narrowing.seam.upper],
                upper_band=narrowing.seam.upper_band,
                lower=cards[narrowing.seam.lower],
                lower_band=narrowing.seam.lower_band,
            )
            if narrowing.seam is not None
            else None
        ),
        choose=narrowing.choose,
        band=narrowing.settled,
    )


# --- Helpers ---


def _moment(placement: Placement | None) -> ComparisonContext:
    """Which moment a judgment belongs to: a first placement, or a re-rate.

    One reader for both writers, so a comparison and the pick that follows it can never
    disagree about which run of the picker they came from.
    """
    return ComparisonContext.re_rate if placement else ComparisonContext.placement


async def _rateable(db: AsyncSession, account: Account, tmdb_id: int) -> AccountFilm:
    """The film's record, refusing anything the owner has not said they watched."""
    account_film: AccountFilm | None = await db.scalar(
        select(AccountFilm).where(
            AccountFilm.account_id == account.id, AccountFilm.film_id == tmdb_id
        )
    )
    if account_film is None or account_film.state is LifecycleState.backlog:
        raise ApiError(409, "not_watched", "Mark this film watched before rating it.")
    return account_film


async def _film(db: AsyncSession, tmdb_id: int) -> Film:
    film = await db.get(Film, tmdb_id)
    if film is None:
        raise ApiError(404, "unknown_film", "That film is not in the catalog.")
    return film
