"""The Discovery screen: the shelf of films the owner has never tracked, and acting on it.

A flat list of about twenty, ordered by the reranker with the linear scorer breaking
ties, each carrying the pitch that was written for it. Position is the entire public
statement (ADR 0005): there are no fit badges, no scores, no ranks, and no "97% match" -
the bucket that decided the order stays on the server, and only the sentence is shown.

*It lights at forming, and never before.* Discovery unlocks a whole readiness state
earlier than the ranked tier does, because anchor designations are the densest taste
signal an account emits and a fresh account needs a backlog filler
(onboarding-and-import.md). Below that the screen simply explains itself and says what it
is waiting for - it does not fabricate a shelf from signal that is not there, and it does
not draw the progress bar either, which surfacing.md gives to the pre-gate Watchlist and
to nothing else.

*Nothing on this path can spend money or wait on anything.* Arriving may queue a restock,
which is the visit-gating discovery.md asks for - an owner who never opens the feed
causes no calls at all - but the response is whatever the last restock left behind. A
short shelf is served short: there is no padding and no degraded-mode banner, because a
feed that only shows what it can stand behind has nothing to apologise for.

*The three actions are three different sentences, and keeping them apart is the point.*
Accept means "I want to watch this" and feeds nothing, because anticipation is not
judgment and the real signal arrives later through watching and placement. Seen-it means
"I already have", which is a fact about the owner's history and not about the pitch.
Dismissal means "this pitch does not appeal" - and it means that reliably only because
the other two exist to take the cases that would otherwise be filed under it (ADR 0006).
Every one of them removes the card at once and backfills the slot from the cache, so the
shelf never has a hole in it and never waits on anything to close one.
"""

import uuid

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from anchor import catalog, jobs
from anchor import feed as feed_module
from anchor import readiness as readiness_module
from anchor import tier as tier_module
from anchor.accounts import CurrentAccount
from anchor.deps import AppJobs, AppSettings, DbSession
from anchor.errors import ApiError
from anchor.models import AccountFilm, Dismissal, Film, LifecycleState, WatchEvent, WatchOrigin
from anchor.models import Suggestion as SuggestionRow
from anchor.profile import Progress
from anchor.readiness import Readiness
from anchor.settings import Settings

router = APIRouter(prefix="/api/discovery")


class Suggestion(BaseModel):
    """One card. Everything the screen draws, and nothing the engine thinks.

    The pitch is precomputed and comes out of the verdict; the plot rides along for the
    spoiler toggle the design puts on every surface that shows one. There is deliberately
    no fit, no bucket, no score and no rank field - not hidden, absent - so no client can
    render one by accident and no future screen can reach for one.
    """

    tmdb_id: int
    title: str
    year: int | None
    poster_path: str | None
    genres: list[str]
    directors: list[str]
    overview: str
    """The TMDB plot summary, shown behind the standard spoiler toggle."""
    pitch: str
    """The exemplar-grounded explanation, visible by default: "because you loved X and Y"."""
    fresh: bool
    """New since the owner's last visit (surfacing.md).

    Freshness, never fit: it says when the card arrived and nothing about how good a match
    it is, which is why it does not touch ADR 0005. It lives on the card and stops there -
    positions are not reordered around it and nothing at nav level ever counts it.
    """

    @classmethod
    def of(cls, shelved: feed_module.Shelved) -> "Suggestion":
        film = shelved.film
        return cls(
            tmdb_id=film.tmdb_id,
            title=film.title,
            year=film.release_year,
            poster_path=film.poster_path,
            genres=list(film.genres),
            directors=catalog.names(film, "directors"),
            overview=film.overview,
            pitch=shelved.verdict.explanation,
            fresh=shelved.fresh,
        )


class Feed(BaseModel):
    """The screen: the shelf, or the honest explanation of why there is not one yet."""

    readiness: Readiness
    unlocked: bool
    """The feed is live. Below *forming* the shelf is empty and ``progress`` says why."""
    progress: Progress | None
    films: list[Suggestion]
    """Up to about twenty, and fewer whenever the pipeline is thin. Never padded."""


class DismissedFilm(BaseModel):
    """One film on the reviewable dismissed list, behind the Discovery overflow."""

    tmdb_id: int
    title: str
    year: int | None
    poster_path: str | None


class Dismissed(BaseModel):
    """Everything the owner has said no to and not taken back, newest first."""

    films: list[DismissedFilm]


class Acted(BaseModel):
    """What an action leaves behind: the shelf as it now stands, and any invite it earned.

    The whole shelf rather than a diff, because the action removed a card *and* backfilled
    the slot behind it, and a client rebuilding that from a 204 would be re-deriving the
    server's ordering rules on the client. One list, already right.
    """

    films: list[Suggestion]
    place_now: bool = False
    """Offer the owner the chance to rate this film now (surfacing.md).

    Seen-it only, and an invite rather than a step: it appears at a moment the owner
    triggered, it is skippable, and skipping it costs them nothing because the film is
    already sitting in the rate-later queue either way.
    """


@router.get("")
async def discovery(
    account: CurrentAccount,
    db: DbSession,
    settings: AppSettings,
    jobs_app: AppJobs,
    boundary: bool = True,
) -> Feed:
    """The shelf as it now stands - which is also the moment it is allowed to change.

    Arriving is the session boundary: the refresh counter moves, cards passed over too
    often rotate off, the shelf is re-derived from the verdict cache, and the
    fresh-since-last-visit line advances by one. The screen reloading after the owner's
    own action says ``boundary=false`` and gets back what is already there, so nothing the
    engine did can move under their cursor mid-session.

    It is also the only read that may queue work. The restock is queued, never awaited: no
    interactive request in Anchor waits on a provider, and this one could not even if it
    wanted to - the module that dispatches is not loaded in this process
    (architecture.md).

    The one-time dot is not this endpoint's business: it is armed and cleared through
    ``unlocks``, which owns both of them, and the screen states its arrival there.
    """
    counted = await readiness_module.evidence(db, account.id)
    state = readiness_module.classify(counted, settings)
    # Below *forming* there is no shelf to change and no restock worth queueing, so the
    # boundary does not run at all: an account that never lights the feed up accumulates
    # no discovery rows, which is what keeps a hollow account free.
    if boundary and state is not Readiness.cold:
        await feed_module.visit(db, account.id, settings)
        if await feed_module.due(db, account.id, settings):
            await jobs.schedule_restock(db, jobs_app, account.id)
        await db.commit()

    if state is Readiness.cold:
        return Feed(
            readiness=state,
            unlocked=False,
            progress=Progress.toward(readiness_module.bars(counted, settings)[Readiness.forming]),
            films=[],
        )
    return Feed(
        readiness=state,
        unlocked=True,
        progress=None,
        films=await _shelf(db, account.id),
    )


@router.post("/{tmdb_id}/accept")
async def accept(
    tmdb_id: int, account: CurrentAccount, db: DbSession, settings: AppSettings
) -> Acted:
    """The owner saying they want to watch this: the film joins the backlog, and nothing learns.

    Accept feeds nothing at all (ADR 0006). A recommender that trains on acceptance of its
    own suggestions amplifies its own biases in a loop, and there is no need to: the taste
    signal arrives later at full fidelity, when the owner watches the film and places it.

    Quarantine is not delay. The film lands in the backlog like any hand-added one and is
    admitted to the ranked tier on the spot under the newly-backlogged exception
    (watchlist.md), so a strong accept can legitimately be up next by the next session -
    through the scorer, on the same terms as everything else. What the feed never does is
    write to the tier itself.
    """
    await _require_shelved(db, account.id, tmdb_id)
    db.add(
        AccountFilm(
            account_id=account.id,
            film_id=tmdb_id,
            state=LifecycleState.backlog,
            # The only place this value is ever written. It is read once, at watch time,
            # to stamp the watch event (evaluation.md), and by nothing else ever.
            origin=WatchOrigin.discovery_accept,
        )
    )
    await db.flush()
    await tier_module.reconcile(db, account.id, settings, admit=tmdb_id)
    return await _acted(db, account.id, settings)


@router.post("/{tmdb_id}/dismissal")
async def dismiss(
    tmdb_id: int, account: CurrentAccount, db: DbSession, settings: AppSettings
) -> Acted:
    """The owner saying no: the film is suppressed until they take it back.

    The one queue action anywhere in Anchor that reaches the taste profile, and it reaches
    only the prose third of it, only as pattern evidence, and only once a pile of them has
    built up (ADR 0006). A single dismissal changes nothing whatsoever - it removes a card
    and suppresses a film, and that is the entire visible effect.

    Permanently until lifted, and the row is the suppression: the shelf, the prefilter and
    the backfill all read it, so a dismissed film cannot come back through any of the three
    doors.

    Nothing here schedules any work at all. A dismissal is read by the next regeneration
    that happens for some other reason, and never buys one: it is the weakest signal in
    the system, and a queue action that could schedule spend would be the only one.
    """
    await _require_shelved(db, account.id, tmdb_id)
    existing = await _dismissal(db, account.id, tmdb_id)
    if existing is None:
        db.add(Dismissal(account_id=account.id, film_id=tmdb_id))
    else:
        existing.lifted_at = None
    await db.flush()
    return await _acted(db, account.id, settings)


@router.post("/{tmdb_id}/seen")
async def seen(
    tmdb_id: int, account: CurrentAccount, db: DbSession, settings: AppSettings
) -> Acted:
    """The owner saying they have already seen it: watched-unrated, a seat, and one offer.

    Split off from dismissal deliberately, and the split is what makes the dismissal
    signal worth anything: with "already watched" filed here, a dismissal reliably means
    the pitch does not appeal rather than "wrong, I have seen it" (discovery.md). Nothing
    about this touches the dismissal record.

    The rate-later seat is taken here rather than offered, exactly as it is when a film is
    marked watched from its own page: it is the resting state of a watched-unrated film,
    and taking it is what makes walking away safe. So the place-it-now invite is a genuine
    offer - skipping it loses nothing, because the film is already waiting in the queue.
    """
    await _require_shelved(db, account.id, tmdb_id)
    account_film = AccountFilm(
        account_id=account.id,
        film_id=tmdb_id,
        state=LifecycleState.watched_unrated,
        rate_later=True,
        # Hand-added, not discovery-accept. The owner watched this film before Anchor ever
        # mentioned it, so attributing the watch to the feed would inflate the accept-rate
        # indicator with watches discovery did not cause (evaluation.md).
        origin=WatchOrigin.hand_added,
    )
    db.add(account_film)
    await db.flush()
    db.add(
        WatchEvent(
            account_id=account.id,
            film_id=tmdb_id,
            standing=tier_module.standing(account_film),
            origin=account_film.origin,
        )
    )
    await db.flush()
    # A film that was never in the backlog frees no seat, but the tier is reconciled all
    # the same: the account's library just grew, and leaving the queue stale until the
    # next boundary would be a worse answer than the one query this costs.
    await tier_module.reconcile(db, account.id, settings)
    return await _acted(db, account.id, settings, place_now=True)


@router.get("/dismissals")
async def dismissals(account: CurrentAccount, db: DbSession) -> Dismissed:
    """The reviewable dismissed list, newest first: what the owner has ruled out.

    Behind the Discovery overflow rather than on the shelf, because it is a record to
    check rather than something to act on - the loudness ceiling for anything that is not
    the shelf itself (surfacing.md).
    """
    rows = await db.execute(
        select(Film)
        .join(Dismissal, Dismissal.film_id == Film.tmdb_id)
        .where(Dismissal.account_id == account.id, Dismissal.lifted_at.is_(None))
        .order_by(Dismissal.created_at.desc(), Dismissal.id)
    )
    return Dismissed(
        films=[
            DismissedFilm(
                tmdb_id=film.tmdb_id,
                title=film.title,
                year=film.release_year,
                poster_path=film.poster_path,
            )
            for film in rows.scalars()
        ]
    )


@router.delete("/{tmdb_id}/dismissal", status_code=204)
async def lift_dismissal(tmdb_id: int, account: CurrentAccount, db: DbSession) -> None:
    """Take a dismissal back. Stamped rather than deleted, the way a correction is.

    The owner changing their mind is itself evidence, and a lifted row says something a
    missing one cannot. The film becomes suggestible again at the next boundary, from
    whatever verdict the cache still holds - so it costs nothing to come back.
    """
    dismissal = await _dismissal(db, account.id, tmdb_id)
    if dismissal is None:
        raise ApiError(404, "not_dismissed", "You have not dismissed that film.")
    dismissal.lifted_at = func.now()
    await db.commit()


# --- Helpers ---


async def _acted(
    db: AsyncSession, account_id: uuid.UUID, settings: Settings, *, place_now: bool = False
) -> Acted:
    """Close the gap the action left, commit, and hand back the shelf it produced.

    The backfill is a pair of queries against verdicts Anchor already paid for, so the
    slot is full by the time the response is written and no provider is involved at any
    point (discovery.md). Nothing here is a session boundary: the cards the owner did not
    touch are left exactly as they were, sentence included.
    """
    await feed_module.backfill(db, account_id, settings)
    await db.commit()
    return Acted(films=await _shelf(db, account_id), place_now=place_now)


async def _shelf(db: AsyncSession, account_id: uuid.UUID) -> list[Suggestion]:
    return [Suggestion.of(shelved) for shelved in await feed_module.shelf(db, account_id)]


async def _require_shelved(db: AsyncSession, account_id: uuid.UUID, tmdb_id: int) -> None:
    """Refuse an action on a film that is not on this owner's shelf.

    The three actions are things the owner does to a *card*, not to a film: they are the
    reasons a suggestion goes away. Accepting a film from anywhere else is the film page's
    "add to backlog", and dismissing one Anchor never suggested would write a suppression
    for a film that was never going to be suggested anyway.
    """
    standing = await db.scalar(
        select(SuggestionRow.id).where(
            SuggestionRow.account_id == account_id, SuggestionRow.film_id == tmdb_id
        )
    )
    if standing is None:
        raise ApiError(404, "not_suggested", "That film is not on your shelf.")
    tracked = await db.scalar(
        select(AccountFilm.id).where(
            AccountFilm.account_id == account_id, AccountFilm.film_id == tmdb_id
        )
    )
    if tracked is not None:
        raise ApiError(409, "already_tracked", "That film is already in your library.")


async def _dismissal(db: AsyncSession, account_id: uuid.UUID, tmdb_id: int) -> Dismissal | None:
    dismissal: Dismissal | None = await db.scalar(
        select(Dismissal).where(Dismissal.account_id == account_id, Dismissal.film_id == tmdb_id)
    )
    return dismissal
