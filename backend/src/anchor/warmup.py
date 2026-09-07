"""Onboarding: the entry fork, and one warmup skeleton with two fills.

Phases in a fixed order - mark anchors, then seed the backlog, with a rate-some-films
step between them on the fresh fill - filled differently depending on whether the account
arrived with a Letterboxd export or with nothing (onboarding-and-import.md). The skeleton
is shared so that neither path is a special case of the other, and so that an owner who
imports later meets the same questions they would have met on day one.

Both fills have three steps and only the middle one differs. The fresh fill rates about
five more films; the import fill looks over the wall its export just built, which is
Rated opening in edit mode with a one-time explanation of dragging and marking. An owner
who imported already has ratings, so asking for more would be asking for the one thing
they came in holding; what they have not seen is the ordering those ratings made
(ADR 0013 removed the settling step that used to stand there).

Two rules run through everything here.

*The warmup is never a gate.* Every prompt is individually skippable, skipping any part
leaves the app fully usable, and nothing downstream asks whether the warmup finished.
What the phases report is therefore progress, not permission: a "done" here means the
question has stopped being worth asking, never that something was unlocked by it.

*Nothing is stored that could be derived.* Which bands have anchors, what is in the
backlog - all of it is read fresh on every request, so the warmup cannot drift out of step
with the account it is describing. The one thing with no other trace is a skip, which
records no judgment by definition, and that is the whole content of
:class:`~anchor.models.WarmupProgress`.

Marking stays the owner's act in both fills. This module ranks candidates; it never marks,
and :mod:`anchor.anchors` is the only writer of an anchor mark there is (ADR 0013).
"""

import enum
import uuid
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from anchor import anchors as anchors_module
from anchor import ordering as ordering_module
from anchor import readiness as readiness_module
from anchor import remembered
from anchor.accounts import CurrentAccount
from anchor.catalog import FilmCard
from anchor.deps import AppSettings, DbSession
from anchor.errors import ApiError
from anchor.models import (
    BANDS,
    AccountFilm,
    Import,
    ImportRow,
    ImportRowKind,
    ImportRowState,
    LifecycleState,
    Placement,
    WarmupMark,
    WarmupProgress,
)
from anchor.ordering import Ordering
from anchor.readiness import Readiness
from anchor.settings import Settings

router = APIRouter(prefix="/api/warmup")

WHOLE_STARS: tuple[float, ...] = (5.0, 1.0, 3.0, 4.0, 2.0)
"""The five whole-star prompts, in the spec's ease-of-recall order.

Best first because a favourite film is the one judgment nobody has to think about, then
worst, then the middle, then the two that are only findable once their neighbours exist.
"""

HALF_STARS: tuple[float, ...] = (4.5, 0.5, 2.5, 3.5, 1.5)
"""The optional continuation, in the same shape: outsides first, then the interior.

Offered rather than prompted, and always after the whole stars, because "a definitive
3.5" is a harder judgment than "a definitive 3" (onboarding-and-import.md).
"""


# --- Wire shapes ---


class Fill(enum.StrEnum):
    """Which fill the shared skeleton is running. Derived from the account, never chosen.

    The entry fork picks a *route*, not a fill: an owner who starts fresh and imports a
    month later gets the import fill from that moment, because the fill is only ever a
    reading of what the account actually holds.
    """

    imported = "imported"
    fresh = "fresh"


class PromptState(enum.StrEnum):
    """Where one prompt stands. Skipped and done are both terminal, and both fine."""

    todo = "todo"
    done = "done"
    skipped = "skipped"


class AnchorPrompt(BaseModel):
    """One band's mark prompt: what has been marked there, and what is worth offering."""

    band: float
    state: PromptState
    marked: list[FilmCard]
    """The band's anchor pool. Any number may be marked, so the prompt is done at one."""
    candidates: list[FilmCard]
    """The account's own films in this band, best-remembered first, minus what is marked.

    Offered on both fills, because these are never suggestions in the recommender sense:
    they are films this owner has already rated into this band, so there is no popularity
    grid wearing the costume of a recommendation. On the import fill that is most of the
    library; on the fresh fill it is whatever the owner has just rated through the picker,
    which is exactly what they came back to mark.

    A marked band keeps its list. Any number may be marked per band, and the film that
    would be the second is sitting in the same ranked list the first came from - so
    closing the offer at one would send the owner to the film page to do what the prompt
    was already doing (onboarding-and-import.md).
    """


class AnchorPhase(BaseModel):
    """Phase 1: give each band a reference, by marking the films the owner knows cold."""

    state: PromptState
    prompts: list[AnchorPrompt]
    """The five whole stars, in ease-of-recall order."""
    continuation: list[AnchorPrompt]
    """The five half stars, offered after the whole stars and never before them."""
    browse: bool
    """Offer the popular/top-rated grid as the explicit "need inspiration?" fallback."""


class RatingPhase(BaseModel):
    """The fresh fill's middle step: "rate ~5 films you have seen", as normal ratings.

    Absent on the import fill, whose middle step is :class:`WallPhase` instead.
    """

    state: PromptState
    rated: int
    target: int
    """Advisory: what the phase stops asking after, never what the owner has to reach."""


class WallPhase(BaseModel):
    """The import fill's middle step: the wall the export just built, in edit mode.

    Absent on the fresh fill, which has nothing yet to look over.
    """

    state: PromptState
    moved: int
    """Films the owner has moved, ever. Read off the placements, like everything here."""
    target: int
    """Advisory: what the step stops asking after. The wall was already theirs to edit.

    The step's one-time explanation is not here. It explains dragging, so it rides the
    screen the step sends the owner to rather than the screen they leave, and it is read
    off the Rated payload's ``wall_hint`` (:func:`explain_the_wall`).
    """


class BacklogPhase(BaseModel):
    """The last phase: something to watch next, the one feature usable from minute one."""

    state: PromptState
    films: int
    seeded: int
    """Backlog films the import's watchlist.csv put there, which is the whole of the
    import fill's last phase: it has already happened by the time the owner sees it."""


class Warmup(BaseModel):
    """The whole warmup, read fresh. Everything but the skips is derived."""

    fill: Fill
    fork: bool
    """Show the entry fork: the owner has not answered it either way yet."""
    dismissed: bool
    anchors: AnchorPhase
    rating: RatingPhase | None
    """The fresh fill's middle step, and None on the import fill, which has the wall."""
    wall: WallPhase | None
    """The import fill's middle step, and None on the fresh fill, which has nothing yet."""
    backlog: BacklogPhase
    readiness: Readiness
    """Ambient only, for the progress line surfacing.md allows on a warmup step."""


class Skip(BaseModel):
    """Skipping one prompt, or a whole phase where no band is named."""

    mark: Literal[WarmupMark.anchors, WarmupMark.rating, WarmupMark.wall, WarmupMark.backlog]
    band: float | None = None


# --- Reading the warmup ---


@router.get("")
async def read(account: CurrentAccount, db: DbSession, settings: AppSettings) -> Warmup:
    return await _warmup(db, account.id, settings)


@router.post("/enter")
async def enter(account: CurrentAccount, db: DbSession, settings: AppSettings) -> Warmup:
    """The entry fork has been answered, whichever way it was answered.

    Both branches land here, because the fork's question is "which way in?" and either
    answer settles it. Which fill runs is read off the account rather than recorded, so
    an owner who picks "start fresh" and imports later is not held to the choice.
    """
    await _mark(db, account.id, WarmupMark.entered)
    await db.commit()
    return await _warmup(db, account.id, settings)


@router.post("/skip")
async def skip(body: Skip, account: CurrentAccount, db: DbSession, settings: AppSettings) -> Warmup:
    """Put one prompt, or one whole phase, away without answering it.

    A skip records no judgment - that is what makes it a skip - so the only thing it
    writes is that this prompt has been asked and should not be asked again.
    """
    if body.band is not None:
        if body.mark is not WarmupMark.anchors:
            raise ApiError(422, "not_a_band_prompt", "Only an anchor prompt names a band.")
        if body.band not in BANDS:
            raise ApiError(422, "not_a_band", "Ratings run from 0.5 to 5.0 in half-stars.")
    await _mark(db, account.id, body.mark, body.band)
    await db.commit()
    return await _warmup(db, account.id, settings)


@router.post("/dismiss")
async def dismiss(account: CurrentAccount, db: DbSession, settings: AppSettings) -> Warmup:
    """Put the whole warmup away. The app was fully usable before this and after it."""
    await _mark(db, account.id, WarmupMark.dismissed)
    await db.commit()
    return await _warmup(db, account.id, settings)


async def _warmup(db: AsyncSession, account_id: uuid.UUID, settings: Settings) -> Warmup:
    marks = await _marks(db, account_id)
    fill = await _fill(db, account_id)
    ordering = await ordering_module.load(db, account_id)
    pools = await anchors_module.pools(db, account_id)

    return Warmup(
        fill=fill,
        fork=(WarmupMark.entered, None) not in marks and (WarmupMark.dismissed, None) not in marks,
        dismissed=(WarmupMark.dismissed, None) in marks,
        anchors=await _anchor_phase(db, account_id, settings, fill, marks, ordering, pools),
        rating=(
            await _rating_phase(db, account_id, settings, marks, pools)
            if fill is Fill.fresh
            else None
        ),
        wall=(
            await _wall_phase(db, account_id, settings, marks) if fill is Fill.imported else None
        ),
        backlog=await _backlog_phase(db, account_id, marks),
        readiness=readiness_module.classify(
            await readiness_module.evidence(db, account_id), settings
        ),
    )


async def _fill(db: AsyncSession, account_id: uuid.UUID) -> Fill:
    """Which fill this account is running: it has an import, or it has not."""
    imported = await db.scalar(select(Import.id).where(Import.account_id == account_id))
    return Fill.imported if imported is not None else Fill.fresh


# --- Phase 1: mark anchors ---


async def _anchor_phase(
    db: AsyncSession,
    account_id: uuid.UUID,
    settings: Settings,
    fill: Fill,
    marks: set[tuple[WarmupMark, float | None]],
    ordering: Ordering,
    pools: dict[float, list[int]],
) -> AnchorPhase:
    ranked = await _candidates(db, account_id, settings, ordering, pools)
    cards = await ordering_module.cards(
        db,
        [
            *(film_id for pool in pools.values() for film_id in pool),
            *(film_id for band in ranked.values() for film_id in band),
        ],
    )

    def prompt(band: float) -> AnchorPrompt:
        pool = pools.get(band, [])
        if pool:
            state = PromptState.done
        elif (WarmupMark.anchors, band) in marks or (WarmupMark.anchors, None) in marks:
            state = PromptState.skipped
        else:
            state = PromptState.todo
        return AnchorPrompt(
            band=band,
            state=state,
            marked=[cards[film_id] for film_id in pool if film_id in cards],
            # A skipped band is the one that stops offering: the owner said stop asking.
            # A marked one has answered the question and is still open to a second mark.
            candidates=(
                []
                if state is PromptState.skipped
                else [cards[film_id] for film_id in ranked.get(band, ()) if film_id in cards]
            ),
        )

    prompts = [prompt(band) for band in WHOLE_STARS]
    return AnchorPhase(
        state=_phase_state(
            skipped=(WarmupMark.anchors, None) in marks,
            done=all(one.state is not PromptState.todo for one in prompts),
        ),
        prompts=prompts,
        continuation=[prompt(band) for band in HALF_STARS],
        # Search is the headline act on the fresh fill and the grid is its stated
        # fallback; on the import fill the owner's own library is the better grid.
        browse=fill is Fill.fresh,
    )


async def _candidates(
    db: AsyncSession,
    account_id: uuid.UUID,
    settings: Settings,
    ordering: Ordering,
    pools: dict[float, list[int]],
) -> dict[float, list[int]]:
    """The films worth offering per band, best-remembered first.

    The ranking is the spec's - rewatch count, then rating recency, with TMDB popularity
    as the tiebreak - and lives in :mod:`anchor.remembered` because more than one screen
    asks which of these the owner remembers clearly enough to judge.
    """
    banded = {
        band: [
            placed.film_id
            for placed in ordering.row(band)
            if placed.film_id not in pools.get(band, ())
        ]
        for band in ordering.bands()
    }
    if not banded:
        return {}
    key = await remembered.ranking(
        db, account_id, [film_id for films in banded.values() for film_id in films]
    )
    return {
        band: sorted(films, key=key)[: settings.warmup_candidates_per_band]
        for band, films in banded.items()
    }


# --- The fresh fill's middle phase: rate some films ---


async def _rating_phase(
    db: AsyncSession,
    account_id: uuid.UUID,
    settings: Settings,
    marks: set[tuple[WarmupMark, float | None]],
    pools: dict[float, list[int]],
) -> RatingPhase:
    """ "Rate ~5 films you have seen", counted past the ones phase 1 already produced.

    Marking an anchor means rating that film first, so those ratings are subtracted:
    asking the owner to rate five *more* is the point of the phase.
    """
    rated = (
        await db.scalar(
            select(func.count())
            .select_from(AccountFilm)
            .where(AccountFilm.account_id == account_id, AccountFilm.state == LifecycleState.rated)
        )
    ) or 0
    marked = sum(len(pool) for pool in pools.values())
    beyond = max(0, rated - marked)
    return RatingPhase(
        state=_phase_state(
            skipped=(WarmupMark.rating, None) in marks,
            done=beyond >= settings.warmup_placements,
        ),
        rated=beyond,
        target=settings.warmup_placements,
    )


# --- The import fill's middle phase: look over the wall ---


async def _wall_phase(
    db: AsyncSession,
    account_id: uuid.UUID,
    settings: Settings,
    marks: set[tuple[WarmupMark, float | None]],
) -> WallPhase:
    """ "Look over the wall": Rated in edit mode, done once a few films have been moved.

    What completes it is having used the gesture rather than having got the ordering
    right, so the count is of moves and the target is advisory. Derived like everything
    else here: a placement carries the moment it was last moved, so the step cannot drift
    out of step with the wall it is describing.
    """
    moved = await _moves(db, account_id)
    return WallPhase(
        state=_phase_state(
            skipped=(WarmupMark.wall, None) in marks, done=moved >= settings.warmup_moves
        ),
        moved=moved,
        target=settings.warmup_moves,
    )


async def _moves(db: AsyncSession, account_id: uuid.UUID) -> int:
    """How many of the account's films sit somewhere the owner put them by hand.

    A placement's ``moved_at`` is None until the first move and set from then on, so this
    counts films ever moved rather than moves ever made - which is the question the step
    is asking, since moving one film twice teaches the gesture exactly once.
    """
    return (
        await db.scalar(
            select(func.count())
            .select_from(Placement)
            .where(Placement.account_id == account_id, Placement.moved_at.is_not(None))
        )
    ) or 0


async def explain_the_wall(db: AsyncSession, account_id: uuid.UUID) -> bool:
    """Whether edit mode should carry the wall step's one-time explanation.

    Lives here rather than on the Rated screen because it is the warmup's own line, and
    the screen it appears on is the one the step sends the owner to: the explanation is
    about dragging, so it has to be waiting where the dragging happens rather than on the
    page they just left.

    Dismissing the whole warmup silences it, which is the one place a dismissal does more
    than close a screen: the phases go on reporting "todo" after one, because put away is
    not answered - but this is the warmup speaking on somebody else's screen, and an owner
    who put the warmup away has said they do not want to be spoken to by it.

    Four facts in one round trip rather than the phase read's several, because this rides
    the Rated screen - the most-read screen there is - and the answer is False on almost
    every load of it.
    """
    imported = select(Import.id).where(Import.account_id == account_id).exists()
    moved = (
        select(Placement.id)
        .where(Placement.account_id == account_id, Placement.moved_at.is_not(None))
        .exists()
    )
    put_away = (
        select(WarmupProgress.id)
        .where(
            WarmupProgress.account_id == account_id,
            WarmupProgress.mark.in_((WarmupMark.wall, WarmupMark.dismissed)),
        )
        .exists()
    )
    return bool(await db.scalar(select(imported & ~moved & ~put_away)))


# --- The last phase: seed the backlog ---


async def _backlog_phase(
    db: AsyncSession, account_id: uuid.UUID, marks: set[tuple[WarmupMark, float | None]]
) -> BacklogPhase:
    films = (
        await db.scalar(
            select(func.count())
            .select_from(AccountFilm)
            .where(
                AccountFilm.account_id == account_id,
                AccountFilm.state == LifecycleState.backlog,
            )
        )
    ) or 0
    seeded = (
        await db.scalar(
            select(func.count(func.distinct(ImportRow.film_id))).where(
                ImportRow.account_id == account_id,
                ImportRow.kind == ImportRowKind.watchlist,
                ImportRow.state.in_((ImportRowState.auto_matched, ImportRowState.bound)),
            )
        )
    ) or 0
    return BacklogPhase(
        state=_phase_state(skipped=(WarmupMark.backlog, None) in marks, done=films > 0),
        films=films,
        seeded=seeded,
    )


# --- The marks ---


async def _marks(db: AsyncSession, account_id: uuid.UUID) -> set[tuple[WarmupMark, float | None]]:
    rows = await db.execute(
        select(WarmupProgress.mark, WarmupProgress.band).where(
            WarmupProgress.account_id == account_id
        )
    )
    return {(mark, band) for mark, band in rows}


async def _mark(
    db: AsyncSession, account_id: uuid.UUID, mark: WarmupMark, band: float | None = None
) -> None:
    """Record a mark once. Marking twice is something the owner is allowed to ask for."""
    if (mark, band) in await _marks(db, account_id):
        return
    db.add(WarmupProgress(account_id=account_id, mark=mark, band=band))


def _phase_state(*, skipped: bool, done: bool) -> PromptState:
    """Skipped wins over done: an owner who put a phase away is not owed a tick for it."""
    if skipped:
        return PromptState.skipped
    return PromptState.done if done else PromptState.todo
