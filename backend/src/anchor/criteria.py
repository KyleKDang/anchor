"""The criteria questions: "Which had the better ___?", asked as a run or a session.

The whole feature is one card, and most of its design is in what it refuses to do.

*The wording is a fixed template.* The intelligence is entirely in selection - which two
films, which quality - and the system never invents a quality or a free-form question.
There is no text generation anywhere in this module and no LLM call.

*It never blocks.* Answering, tapping Tied, dismissing the card, or simply navigating
away all cost the owner the same, which means the offer itself has to be the record: the
row is written when the card is shown, reading ``skip``, and only an answer changes it.
An ignored card and a dismissed card are then literally the same row, which is what the
spec asks for, and unanswered offers are exactly what the adaptive frequency reads.

*It never moves the ordering* (ADR 0007). Criteria answers are loose evidence about
taste and nothing more: no per-quality ordering exists, shown or internal. Structurally
that holds because every consumer of the log - the trainer's pair extraction, the film
page's history, the frequency dial - names the kind of row it wants, so a criteria row is
invisible to all of them by construction rather than by anyone remembering.

*Two homes, one card* (taste-profile.md, screens-and-flows.md). The **run** rides on the
done screen: the landing carries the first card when the frequency setting allows one,
and each answer mints the next, until the owner dismisses, leaves, or nothing unasked
remains. The **session** opens from a rated film's own page, whatever the frequency
says: the same cards about that one film against varied opponents, until the owner
leaves or nothing unasked remains. An answer is the only thing that mints a next card in
either home, so there is no call that can put a card in front of the owner they did not
earn by answering the last, and nothing to send when they stop.

*A question about a film is asked once.* The set a card is chosen against is everything
already asked about its subject, whichever home asked it: a session never re-asks what
the run just asked, and "nothing unasked remains" means exactly that. The same pair can
still be asked again from the other film's side, which is where a later, contradicting
answer comes from (taste-profile.md).
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from anchor import ordering as ordering_module
from anchor import qualities, remembered, tags
from anchor.accounts import CurrentAccount
from anchor.catalog import FilmCard
from anchor.deps import AppJobs, AppSettings, DbSession
from anchor.errors import ApiError
from anchor.models import (
    BUILT_IN_QUALITIES,
    Account,
    ComparisonContext,
    ComparisonKind,
    ComparisonLogEntry,
    ComparisonVerdict,
    CriteriaFrequency,
    QualityListEntry,
)

router = APIRouter(prefix="/api/criteria")

STEP = 1
"""One rating: the unit the gaps are counted in.

Frequency used to be denominated in answered comparisons, because the log counted those
exactly and held no landing record to count instead. It holds one now - a band pick is a
rating, one row per - and a rating is what a run follows, so the gap is counted in the
thing it gates rather than in a proxy for it (ADR 0013).
"""

MANUAL_GAPS: dict[CriteriaFrequency, int] = {
    CriteriaFrequency.often: 0,
    CriteriaFrequency.sometimes: STEP,
    CriteriaFrequency.rarely: 4 * STEP,
}
"""How many ratings must pass between runs, per manual setting. ``off`` is absent
because it is not a gap: no card is ever offered and no offer is ever recorded."""

ADAPTIVE_WINDOW = 4
"""How many recent events the adaptive setting reads. Short on purpose: it should follow
a change of heart within a few placements rather than average over a whole history."""

OFFERED = ComparisonVerdict.skip
"""What an offer says before the owner says anything: no judgment, on purpose."""

CANDIDATES = 12
"""How far down the ladder a card looks for its opponent.

The ladder's last rung is the whole rated library, which is the right answer for
*selection* - there is always something to ask about - and the wrong one for everything
downstream of it: the tag-sharing preference reads a tag for every candidate, and the
landing buys tags for every film a card could have named. Unbounded, one rating would
pay to tag a six-hundred-row import.

Cutting it here costs nothing the ladder was buying. The rungs are ordered by how well
this owner knows the film, so the twelfth candidate is already well past the ones a
question would land on, and the fallback below the cut is the same fallback the ladder
already has when nothing shares a tag. It also bounds a session: twelve opponents times
the quality list is where "nothing unasked remains" lands.
"""

RATINGS = (ComparisonContext.placement, ComparisonContext.re_rate)
"""The two moments a run is offered at: a rating, or a re-rate (taste-profile.md).

These are the run's contexts, and the only offers the frequency dial reads. The session
from a film's page is recorded as ``spontaneous``: it is pull-only and unbounded, so the
dial has no business governing it, and its offers are not offers the dial counts.
"""

SESSION = ComparisonContext.spontaneous
"""The session's context: a card the owner asked for, from the film's own page."""


class CriteriaAnswer(BaseModel):
    """One answer to a card.

    ``skip`` is absent on purpose: not answering is the card being left alone, which is
    already what the row says, so there is nothing for the client to send.
    """

    verdict: Literal["a", "b", "tied"]


class CriteriaCard(BaseModel):
    """The card: one quality, the two films, and nothing else to decide."""

    id: uuid.UUID
    quality: str
    """The list entry's name, dropped into the fixed template by the client."""
    film_a: FilmCard
    film_b: FilmCard


class Answered(BaseModel):
    """What an answer hands back: the next card in the same home, or None when it is over.

    Over means the run's frequency was switched off, or nothing unasked remains about the
    film. The owner leaving or dismissing is not something the server hears about, and
    does not need to: no answer, no next card.
    """

    next: CriteriaCard | None


class Session(BaseModel):
    """A session's opening: its first card, or None when there is nothing left to ask."""

    card: CriteriaCard | None


@dataclass(frozen=True)
class Matchup:
    """A pair a card could ask about: the subject, and one film to set it against."""

    film_a: int
    film_b: int


# --- Offering ---


async def offer(
    db: AsyncSession,
    account: Account,
    subject: int,
    context: ComparisonContext,
) -> CriteriaCard | None:
    """The run's first card, riding on this landing, or None - the ordinary outcome.

    The frequency setting governs this call and nothing else in the module: whether a
    run starts is the dial's business, and once it has started each answer brings the
    next card without asking the dial again.

    The row this writes is flushed, not committed: the caller commits it with the landing,
    so the two stand or fall together.
    """
    if context not in RATINGS:
        return None
    if account.criteria_frequency is CriteriaFrequency.off:
        return None
    if not await _due(db, account):
        return None
    return await _mint(db, account.id, subject, context)


async def askable_films(db: AsyncSession, account_id: uuid.UUID, subject: int) -> list[int]:
    """Every film a card about ``subject`` could name, each once.

    Exported for the quality tagging, which buys tags for exactly this set: the films
    selection will look tags up for next time. Deliberately the same derivation the card
    itself uses rather than a similar one, so a tagging never buys tags for films no card
    here can ask about - nor misses the ones it will.
    """
    return sorted(
        {
            film
            for matchup in await _candidates(db, account_id, subject)
            for film in (matchup.film_a, matchup.film_b)
        }
    )


async def _mint(
    db: AsyncSession, account_id: uuid.UUID, subject: int, context: ComparisonContext
) -> CriteriaCard | None:
    """Put the next card about ``subject`` in front of the owner, or None if there is none.

    The candidates are the films the owner is likely to remember beside the subject,
    down the ladder taste-profile.md fixes: the subject's own band's anchors first, then
    its neighbours on the wall, then the films it was set against in the picker, then
    the wider library. Every rung is a film the owner has rated, because the question is
    which of two films did something better and they have to remember both.

    Which of those candidates, and which quality, is :func:`_select`. None comes back
    when there is no opponent at all, no quality list, or nothing left that has not been
    asked about this film - which is how both homes end of their own accord.

    Flushed, not committed: the caller owns the transaction.
    """
    candidates = await _candidates(db, account_id, subject)
    if not candidates:
        return None
    listed = await qualities.listing(db, account_id)
    if not listed:
        return None
    chosen = await _select(db, account_id, subject, candidates, listed)
    if chosen is None:
        return None
    matchup, quality = chosen
    entry = ComparisonLogEntry(
        account_id=account_id,
        kind=ComparisonKind.criteria,
        subject_film_id=subject,
        film_a_id=matchup.film_a,
        film_b_id=matchup.film_b,
        verdict=OFFERED,
        quality_id=quality.id,
        context=context,
    )
    db.add(entry)
    await db.flush()
    return await _card(db, entry)


async def _candidates(db: AsyncSession, account_id: uuid.UUID, subject: int) -> list[Matchup]:
    """The films to set the subject against, best candidate first (taste-profile.md).

    The order is the point, and it is a claim about memory rather than about quality: an
    anchor of the subject's own band is the film this owner is most certain of, its
    neighbours on the wall are the films it sits between, the films the picker set it
    against are ones they judged it beside, and the rest of the library is the fallback
    that keeps the card possible at all. Inside the last rung the best-remembered film
    wins, which is the same ranking the warmup's candidates use.
    """
    ordering = await ordering_module.load(db, account_id)
    placed = ordering.of(subject)
    rungs: list[int] = []
    if placed is not None:
        rungs += list(ordering.anchors(placed.band))
        rungs += [film for film in ordering.neighbours(subject) if film is not None]
    rungs += await _compared_against(db, account_id, subject)
    rest = [film for film in ordering.all_film_ids() if film not in rungs and film != subject]
    key = await remembered.ranking(db, account_id, rest)
    rungs += sorted(rest, key=key)
    # Deduped in place: a film can be an anchor and a neighbour, and the ladder is an
    # order of preference rather than a set of tiers.
    ranked = [film for film in dict.fromkeys(rungs) if film != subject]
    return [Matchup(subject, film) for film in ranked[:CANDIDATES]]


async def _compared_against(db: AsyncSession, account_id: uuid.UUID, subject: int) -> list[int]:
    """The films the subject was set against while being rated, most recent first."""
    rows = await db.execute(
        select(ComparisonLogEntry.film_a_id, ComparisonLogEntry.film_b_id)
        .where(
            ComparisonLogEntry.account_id == account_id,
            ComparisonLogEntry.kind == ComparisonKind.band_comparison,
            ComparisonLogEntry.subject_film_id == subject,
            ComparisonLogEntry.film_b_id.is_not(None),
        )
        .order_by(ComparisonLogEntry.created_at.desc())
    )
    return [b if a == subject else a for a, b in rows]


async def _select(
    db: AsyncSession,
    account_id: uuid.UUID,
    subject: int,
    candidates: list[Matchup],
    listed: list[QualityListEntry],
) -> tuple[Matchup, QualityListEntry] | None:
    """Which pair to ask about, and which quality to ask (taste-profile.md).

    *Never the same pair and quality twice.* Everything already asked about the subject
    is off the table, whichever home asked it and whether or not it was answered; when
    that leaves nothing, None ends the run or the session.

    *Varied opponents.* The candidates are taken least-asked first, the ladder's own order
    inside a tie, so a session sets the film against each of its opponents before it
    comes back round to ask a second thing about any of them. On a fresh film that is
    simply the ladder.

    *Prefer the pair whose films share a quality tag, and ask about that quality.* Two
    films both known for their tension make "which had the better tension?" a question
    about a real difference, where the same question about a film that is not notable
    for it is a question the owner has to invent an answer to.

    *Otherwise rotate through the quality list on the best candidate* - which is also
    what happens when nothing has been tagged yet, when the caps are spent, and when the
    two films simply have nothing in common. The fallback is the ordinary case, not the
    error case.

    Tags name built-in vocabulary only, so a shared tag is asked about only if this
    account still has that quality on its list; the rotation is where a custom quality
    can be asked at all, and it stays the only route to one.
    """
    asked = await _asked(db, account_id, subject)
    films = {film for matchup in candidates for film in (matchup.film_a, matchup.film_b)}
    tagged = await tags.by_film(db, films)
    last = await _last_asked(db, account_id)
    askable = {entry.name: entry for entry in listed}
    # A stable sort, so the ladder's order is what breaks a tie in how often each
    # opponent has been asked about.
    varied = sorted(candidates, key=lambda matchup: len(asked[matchup.film_b]))
    for matchup in varied:
        shared = [
            askable[name]
            for name in BUILT_IN_QUALITIES
            if name in askable
            and name in tagged[matchup.film_a] & tagged[matchup.film_b]
            and askable[name].id not in asked[matchup.film_b]
        ]
        if shared:
            # Which of several shared tags to ask about is not spec'd, so it is settled
            # the same way the fallback is: the one this owner has gone longest without
            # being asked.
            return matchup, _rotated(shared, last)
    for matchup in varied:
        unasked = [entry for entry in listed if entry.id not in asked[matchup.film_b]]
        if unasked:
            return matchup, _rotated(unasked, last)
    return None


async def _asked(
    db: AsyncSession, account_id: uuid.UUID, subject: int
) -> defaultdict[int, set[uuid.UUID]]:
    """Every question already asked about the subject: opponent to the qualities asked.

    Offered is asked. An ignored card was put in front of the owner and they said
    nothing, and asking it again would be the chore the spec rules out.
    """
    rows = await db.execute(
        select(
            ComparisonLogEntry.film_a_id,
            ComparisonLogEntry.film_b_id,
            ComparisonLogEntry.quality_id,
        ).where(
            ComparisonLogEntry.account_id == account_id,
            ComparisonLogEntry.kind == ComparisonKind.criteria,
            ComparisonLogEntry.subject_film_id == subject,
        )
    )
    asked: defaultdict[int, set[uuid.UUID]] = defaultdict(set)
    for a, b, quality_id in rows:
        asked[b if a == subject else a].add(quality_id)
    return asked


def _rotated(listed: list[QualityListEntry], asked: dict[uuid.UUID, datetime]) -> QualityListEntry:
    """The entry this owner has gone longest without being asked, or has never been.

    Rotation rather than sampling, so the list is worked through evenly - the point is
    breadth of evidence across qualities, and a sampler would ask about Acting four times
    before it ever mentioned Pacing.

    Read as "longest unasked" rather than as a cursor into the list, because a cursor
    counting offers would be spent by offers it did not choose: every tag-driven question
    would consume a rotation slot without asking that slot's quality, and the walk would
    develop holes exactly where the list is longest - at the end, where an owner's own
    custom qualities sit, whose only route to a card this is. With nothing tagged the two
    readings agree, and the walk is the list in its own order.
    """
    never = datetime.min.replace(tzinfo=UTC)
    return min(listed, key=lambda entry: asked.get(entry.id, never))


async def _last_asked(db: AsyncSession, account_id: uuid.UUID) -> dict[uuid.UUID, datetime]:
    """When each quality was last put in front of this owner; absent means never."""
    rows = await db.execute(
        select(ComparisonLogEntry.quality_id, func.max(ComparisonLogEntry.created_at))
        .where(
            ComparisonLogEntry.account_id == account_id,
            ComparisonLogEntry.kind == ComparisonKind.criteria,
            ComparisonLogEntry.quality_id.is_not(None),
        )
        .group_by(ComparisonLogEntry.quality_id)
    )
    return {quality_id: when for quality_id, when in rows}


# --- How often a run is offered ---


async def _due(db: AsyncSession, account: Account) -> bool:
    """Whether enough ratings have passed since the last run to offer another."""
    if await _offers(db, account.id) == 0:
        # Nothing to wait behind: the first rating that can produce a pair gets a card
        # whatever the setting, so the owner sees what they are being offered to opt out
        # of rather than having to find a control for a feature they have never met.
        return True
    gap = (
        await _adaptive_gap(db, account.id)
        if account.criteria_frequency is CriteriaFrequency.adaptive
        else MANUAL_GAPS[account.criteria_frequency]
    )
    return await _ratings_since_last_offer(db, account.id) >= gap


async def _adaptive_gap(db: AsyncSession, account_id: uuid.UUID) -> int:
    """The gap engagement has earned: answered cards shorten it, ignored ones lengthen it.

    A short window and a coarse ladder, deliberately. This is a politeness dial, not a
    measurement: what it has to get right is backing off from an owner who keeps walking
    past the card, and coming back for one who keeps answering.

    The window is the run's offers, answered or not, and the session's *answers*: a
    session's card was asked for rather than offered, so walking away from it says
    nothing about the run's welcome, but answering it is engagement wherever it happens
    and counts the same way an answered run card does.
    """
    recent = await db.scalars(
        select(ComparisonLogEntry.verdict)
        .where(
            ComparisonLogEntry.account_id == account_id,
            ComparisonLogEntry.kind == ComparisonKind.criteria,
            or_(
                ComparisonLogEntry.context.in_(RATINGS),
                ComparisonLogEntry.verdict != OFFERED,
            ),
        )
        .order_by(ComparisonLogEntry.created_at.desc(), ComparisonLogEntry.id.desc())
        .limit(ADAPTIVE_WINDOW)
    )
    window = list(recent)
    if not window:
        return STEP
    engaged = sum(1 for verdict in window if verdict is not OFFERED) / len(window)
    # Half, not most: a run ends on a card nobody answered unless the film ran out of
    # questions, so an owner who answers one card and leaves reads as half engaged, and
    # they are exactly the owner the dial must keep coming back for.
    if engaged >= 0.5:
        return 0
    if engaged > 0:
        return STEP
    return 4 * STEP


async def _ratings_since_last_offer(db: AsyncSession, account_id: uuid.UUID) -> int:
    """Ratings that have passed since the last run was offered, not counting this one.

    The rating being made has already written its pick when this is read, and it is the
    rating the run would ride on rather than one that passed in between - so it is taken
    back out, and a gap of one means one rating goes by without a card.
    """
    last = await db.scalar(
        select(func.max(ComparisonLogEntry.created_at)).where(
            ComparisonLogEntry.account_id == account_id,
            ComparisonLogEntry.kind == ComparisonKind.criteria,
            ComparisonLogEntry.context.in_(RATINGS),
        )
    )
    assert last is not None  # only called once a run has been offered
    count = await db.scalar(
        select(func.count())
        .select_from(ComparisonLogEntry)
        .where(
            ComparisonLogEntry.account_id == account_id,
            ComparisonLogEntry.kind == ComparisonKind.band_pick,
            ComparisonLogEntry.created_at > last,
        )
    )
    return max(int(count or 0) - 1, 0)


async def _offers(db: AsyncSession, account_id: uuid.UUID) -> int:
    """Run cards offered to this account ever: the first-run test."""
    count = await db.scalar(
        select(func.count())
        .select_from(ComparisonLogEntry)
        .where(
            ComparisonLogEntry.account_id == account_id,
            ComparisonLogEntry.kind == ComparisonKind.criteria,
            ComparisonLogEntry.context.in_(RATINGS),
        )
    )
    return int(count or 0)


async def _card(db: AsyncSession, entry: ComparisonLogEntry) -> CriteriaCard | None:
    """Dress an offer row as the card, or None once it has been answered."""
    if entry.verdict is not OFFERED:
        return None
    assert entry.film_b_id is not None  # an offer is always about a pair
    quality = await db.get(QualityListEntry, entry.quality_id)
    assert quality is not None  # the check constraint fills it, and RESTRICT keeps it
    films = await ordering_module.cards(db, [entry.film_a_id, entry.film_b_id])
    return CriteriaCard(
        id=entry.id,
        quality=quality.name,
        film_a=films[entry.film_a_id],
        film_b=films[entry.film_b_id],
    )


# --- The session ---


@router.post("/session/{tmdb_id}")
async def open_session(
    tmdb_id: int,
    account: CurrentAccount,
    db: DbSession,
    queue: AppJobs,
    settings: AppSettings,
) -> Session:
    """Open a session about one rated film: its first card, or None if nothing is left.

    Pull-only, and available whatever the frequency setting says: the dial governs the
    run, and an owner who came to a film's page to answer questions about it has already
    said what they want. The card is minted here and recorded as ``spontaneous``, so the
    dial never mistakes it for an offer of its own.
    """
    if await ordering_module.placement_of(db, account.id, tmdb_id) is None:
        raise ApiError(404, "not_rated", "Questions are about films you have rated.")
    card = await _mint(db, account.id, tmdb_id, SESSION)
    # The films this session can ask about are the ones its later cards will want tags
    # for. Precompute only: nothing here waits on the job this queues.
    await tags.schedule(
        db, queue, account.id, await askable_films(db, account.id, tmdb_id), settings
    )
    await db.commit()
    return Session(card=card)


# --- Answering and dismissing ---


@router.post("/{offer_id}")
async def answer(
    offer_id: uuid.UUID, body: CriteriaAnswer, account: CurrentAccount, db: DbSession
) -> Answered:
    """Answer a card, and take the next one in the same home. Optional by construction.

    There is no matching dismiss or leave endpoint, and there should not be. Dismissing
    and ignoring are required to be recorded identically, and the row already records
    both: it says ``skip`` from the moment the card was offered, and only this call
    changes it. The next card exists only because this one was answered, so an owner who
    stops is simply never handed another.
    """
    entry = await _offered(db, account, offer_id)
    entry.verdict = ComparisonVerdict(body.verdict)
    following = None
    # The run answers to the dial's off switch even mid-run, because off is complete;
    # the session answers to nothing but the owner.
    if entry.context is SESSION or account.criteria_frequency is not CriteriaFrequency.off:
        following = await _mint(db, account.id, entry.subject_film_id, entry.context)
    await db.commit()
    return Answered(next=following)


@router.post("/{offer_id}/dismiss")
async def dismiss(offer_id: uuid.UUID, account: CurrentAccount, db: DbSession) -> Answered:
    """Wave a card away: in a session the next one comes, in a run nothing does.

    The card itself is not touched. Dismissing and ignoring are required to be recorded
    identically, and the row already reads as unanswered, so there is nothing to write -
    which is also why a run needs no call for this at all: dismissing ends the run, and
    ending it is the absence of an answer. The endpoint exists for the session, where a
    question the owner cannot answer must be something they can pass over without either
    inventing an answer or leaving; the passed-over card counts as asked, so it never
    comes round again.
    """
    entry = await _offered(db, account, offer_id)
    if entry.context is not SESSION:
        return Answered(next=None)
    following = await _mint(db, account.id, entry.subject_film_id, entry.context)
    await db.commit()
    return Answered(next=following)


async def _offered(db: AsyncSession, account: Account, offer_id: uuid.UUID) -> ComparisonLogEntry:
    """The card this owner was offered and has not answered, or the refusal."""
    entry = await db.get(ComparisonLogEntry, offer_id)
    if entry is None or entry.account_id != account.id or entry.kind is not ComparisonKind.criteria:
        raise ApiError(404, "no_such_offer", "That question is not one you were asked.")
    if entry.verdict is not OFFERED:
        # Nothing is overwritten: a second answer would erase the first, and the log
        # keeps every judgment the owner has made. Changing their mind is a later offer.
        raise ApiError(409, "already_answered", "You have already answered this question.")
    return entry
