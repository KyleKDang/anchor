"""The optional bonus card after a placement: "Which had the better ___?"

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
that holds because every consumer of the log - the placement search, the trainer's pair
extraction, readiness, the band machinery - filters to ``overall`` rows, so a criteria
row is invisible to all of them by construction rather than by anyone remembering.

*At most one per placement.* The card is delivered in the response that lands the film
and nowhere else, so re-visiting the done screen cannot produce a second, and once the
offer row exists the same landing repeated returns the same card rather than a new one.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from anchor import ordering as ordering_module
from anchor import qualities, tags
from anchor.accounts import CurrentAccount
from anchor.catalog import FilmCard
from anchor.deps import DbSession
from anchor.errors import ApiError
from anchor.models import (
    BUILT_IN_QUALITIES,
    Account,
    ComparisonContext,
    ComparisonKind,
    ComparisonLogEntry,
    ComparisonStatus,
    ComparisonVerdict,
    CriteriaFrequency,
    QualityListEntry,
)

router = APIRouter(prefix="/api/criteria")

STEP = 6
"""One placement's worth of comparisons, roughly: the unit the gaps are counted in.

Frequency is denominated in answered comparisons rather than in placements because the
log counts comparisons exactly and holds no landing record to count instead - and a gap
measured in questions the owner actually answered tracks their engagement more honestly
than one measured in films anyway.
"""

MANUAL_GAPS: dict[CriteriaFrequency, int] = {
    CriteriaFrequency.often: 0,
    CriteriaFrequency.sometimes: STEP,
    CriteriaFrequency.rarely: 4 * STEP,
}
"""How many comparisons must pass between offers, per manual setting. ``off`` is absent
because it is not a gap: no card is ever offered and no offer is ever recorded."""

ADAPTIVE_WINDOW = 4
"""How many recent offers the adaptive setting reads. Short on purpose: it should follow
a change of heart within a few placements rather than average over a whole history."""

OFFERED = ComparisonVerdict.skip
"""What an offer says before the owner says anything: no judgment, on purpose."""

PLACEMENTS = (ComparisonContext.placement, ComparisonContext.re_placement)
"""The only two moments a card is offered at: the end of a placement, or of a re-placement.

Keep-comparing and drift checks are deliberately excluded (taste-profile.md). They return
to the same done screen, but they are not a placement ending, and treating them as one is
how "at most one card per placement" would quietly become several.
"""


class CriteriaAnswer(BaseModel):
    """One answer to the bonus card.

    ``skip`` is absent on purpose: not answering is the card being left alone, which is
    already what the row says, so there is nothing for the client to send.
    """

    verdict: Literal["a", "b", "tied"]


class CriteriaCard(BaseModel):
    """The bonus card: one quality, the two films, and nothing else to decide."""

    id: uuid.UUID
    quality: str
    """The list entry's name, dropped into the fixed template by the client."""
    film_a: FilmCard
    film_b: FilmCard


@dataclass(frozen=True)
class Matchup:
    """A pair the owner judged during the placement that just finished."""

    film_a: int
    film_b: int


# --- Offering ---


async def offer(
    db: AsyncSession,
    account: Account,
    subject: int,
    context: ComparisonContext,
    since: datetime | None,
    entries: list[ComparisonLogEntry],
) -> CriteriaCard | None:
    """The card this landing earns, or None - which is the ordinary outcome.

    ``entries`` are the flow's comparisons, oldest first, and ``context`` and ``since``
    say which of them the flow actually collected. The two are not the same list: a
    re-placement resumes from judgments other flows produced, and a settle resumes from
    every judgment the film has ever collected (rating-system.md), so ``entries`` can
    hold work the owner did weeks ago for some other film. The card says "you just
    compared these two", so the matchup is drawn from the collected ones alone - a head
    start is evidence, never a matchup. The row this writes is flushed, not committed:
    the caller commits it with the landing, so the two stand or fall together.
    """
    if context not in PLACEMENTS:
        return None

    standing = await _offer_of_flow(db, account.id, subject, context, since)
    if standing is not None:
        # This landing already made its offer; repeating the request re-shows the same
        # card rather than minting a second one.
        return await _card(db, standing)

    if account.criteria_frequency is CriteriaFrequency.off:
        return None
    candidates = _matchups(_collected(entries, context, since))
    if not candidates:
        return None
    listed = await qualities.listing(db, account.id)
    if not listed:
        return None
    if not await _due(db, account, await _offers(db, account.id)):
        return None

    matchup, quality = await _select(db, account.id, candidates, listed)
    entry = ComparisonLogEntry(
        account_id=account.id,
        kind=ComparisonKind.criteria,
        subject_film_id=subject,
        film_a_id=matchup.film_a,
        film_b_id=matchup.film_b,
        verdict=OFFERED,
        quality_id=quality.id,
        context=context,
        status=ComparisonStatus.active,
    )
    db.add(entry)
    await db.flush()
    return await _card(db, entry)


def askable_films(
    entries: list[ComparisonLogEntry], context: ComparisonContext, since: datetime | None
) -> list[int]:
    """Every film a card from this landing could name, each once.

    Exported for the quality tagging, which buys tags for exactly this set: the films
    selection will look tags up for next time. It is deliberately the same derivation the
    card itself uses rather than a similar one - a re-placement resumes from judgments
    other flows produced, and :func:`_collected` drops them, so a tagging that read the
    raw ``entries`` would buy tags for films no card here can ever ask about.
    """
    return sorted(
        {
            film
            for matchup in _matchups(_collected(entries, context, since))
            for film in (matchup.film_a, matchup.film_b)
        }
    )


def _collected(
    entries: list[ComparisonLogEntry], context: ComparisonContext, since: datetime | None
) -> list[ComparisonLogEntry]:
    """The judgments this flow put on screen, dropping the head start it resumed from.

    Scoped the same way the flow's own answers are, because that is what the flow asked:
    a card naming a pair the owner never saw in this flow would be a bonus for a
    placement that earned nothing, and would ask about a comparison they made for some
    other film entirely.
    """
    return [
        entry
        for entry in entries
        if entry.context is context and (since is None or entry.created_at > since)
    ]


def _matchups(entries: list[ComparisonLogEntry]) -> list[Matchup]:
    """Every pair the owner actually judged in this flow, most recent first.

    Skips are not judgments and so are not matchups: the card asks about a pair the owner
    compared, and a skipped pair is one they declined to. Most recent first because that
    is the order selection reads them in - the tie-break is toward the freshest memory.
    """
    return [
        Matchup(entry.film_a_id, entry.film_b_id)
        for entry in reversed(entries)
        if entry.film_b_id is not None and entry.verdict is not ComparisonVerdict.skip
    ]


async def _select(
    db: AsyncSession,
    account_id: uuid.UUID,
    candidates: list[Matchup],
    listed: list[QualityListEntry],
) -> tuple[Matchup, QualityListEntry]:
    """Which pair to ask about, and which quality to ask (taste-profile.md).

    *Prefer the pair whose films share a quality tag, tie-broken toward the most recent
    matchup.* Two films both known for their tension make "which had the better tension?"
    a question about a real difference, where the same question about a film that is not
    notable for it is a question the owner has to invent an answer to. ``candidates`` is
    already most recent first, so the first overlap found is the tie-break's own answer.

    *If no pair overlaps, rotate through the quality list on the last matchup* - which is
    also what happens when nothing has been tagged yet, when the caps are spent, and when
    the two films simply have nothing in common. The fallback is the ordinary case, not
    the error case.

    Tags name built-in vocabulary only, so a shared tag is asked about only if this
    account still has that quality on its list; the rotation is where a custom quality
    can be asked at all, and it stays the only route to one.
    """
    films = {film for matchup in candidates for film in (matchup.film_a, matchup.film_b)}
    tagged = await tags.by_film(db, films)
    asked = await _last_asked(db, account_id)
    askable = {entry.name: entry for entry in listed}
    for matchup in candidates:
        shared = [
            askable[name]
            for name in BUILT_IN_QUALITIES
            if name in askable and name in tagged[matchup.film_a] & tagged[matchup.film_b]
        ]
        if shared:
            # Which of several shared tags to ask about is not spec'd, so it is settled
            # the same way the fallback is: the one this owner has gone longest without
            # being asked. Placing the same pair twice then asks about something else.
            return matchup, _rotated(shared, asked)
    return candidates[0], _rotated(listed, asked)


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


async def _due(db: AsyncSession, account: Account, made: int) -> bool:
    """Whether enough comparisons have passed since the last offer to make another."""
    if made == 0:
        # Nothing to wait behind: the first placement that produces a matchup gets a card
        # whatever the setting, so the owner sees what they are being offered to opt out
        # of rather than having to find a control for a feature they have never met.
        return True
    gap = (
        await _adaptive_gap(db, account.id)
        if account.criteria_frequency is CriteriaFrequency.adaptive
        else MANUAL_GAPS[account.criteria_frequency]
    )
    return await _comparisons_since_last_offer(db, account.id) >= gap


async def _adaptive_gap(db: AsyncSession, account_id: uuid.UUID) -> int:
    """The gap engagement has earned: answered offers shorten it, ignored ones lengthen it.

    A short window and a coarse ladder, deliberately. This is a politeness dial, not a
    measurement: what it has to get right is backing off from an owner who keeps walking
    past the card, and coming back for one who keeps answering.
    """
    recent = await db.scalars(
        select(ComparisonLogEntry.verdict)
        .where(
            ComparisonLogEntry.account_id == account_id,
            ComparisonLogEntry.kind == ComparisonKind.criteria,
        )
        .order_by(ComparisonLogEntry.created_at.desc(), ComparisonLogEntry.id.desc())
        .limit(ADAPTIVE_WINDOW)
    )
    window = list(recent)
    if not window:
        return STEP
    engaged = sum(1 for verdict in window if verdict is not OFFERED) / len(window)
    if engaged >= 0.75:
        return 0
    if engaged >= 0.25:
        return STEP
    if engaged > 0:
        return 2 * STEP
    return 4 * STEP


async def _comparisons_since_last_offer(db: AsyncSession, account_id: uuid.UUID) -> int:
    """Comparisons the owner has answered since the last card was put in front of them."""
    last = await db.scalar(
        select(func.max(ComparisonLogEntry.created_at)).where(
            ComparisonLogEntry.account_id == account_id,
            ComparisonLogEntry.kind == ComparisonKind.criteria,
        )
    )
    assert last is not None  # only called once an offer has been made
    count = await db.scalar(
        select(func.count())
        .select_from(ComparisonLogEntry)
        .where(
            ComparisonLogEntry.account_id == account_id,
            ComparisonLogEntry.kind == ComparisonKind.overall,
            ComparisonLogEntry.verdict != ComparisonVerdict.skip,
            ComparisonLogEntry.created_at > last,
        )
    )
    return int(count or 0)


async def _offers(db: AsyncSession, account_id: uuid.UUID) -> int:
    """Cards offered to this account ever: the rotation's cursor, and the first-run test."""
    count = await db.scalar(
        select(func.count())
        .select_from(ComparisonLogEntry)
        .where(
            ComparisonLogEntry.account_id == account_id,
            ComparisonLogEntry.kind == ComparisonKind.criteria,
        )
    )
    return int(count or 0)


async def _offer_of_flow(
    db: AsyncSession,
    account_id: uuid.UUID,
    subject: int,
    context: ComparisonContext,
    since: datetime | None,
) -> ComparisonLogEntry | None:
    """This flow's own offer, scoped exactly as the flow's comparisons are."""
    query = select(ComparisonLogEntry).where(
        ComparisonLogEntry.account_id == account_id,
        ComparisonLogEntry.kind == ComparisonKind.criteria,
        ComparisonLogEntry.subject_film_id == subject,
        ComparisonLogEntry.context == context,
    )
    if since is not None:
        query = query.where(ComparisonLogEntry.created_at > since)
    entry: ComparisonLogEntry | None = await db.scalar(
        query.order_by(ComparisonLogEntry.created_at.desc(), ComparisonLogEntry.id.desc()).limit(1)
    )
    return entry


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


# --- Answering ---


@router.post("/{offer_id}", status_code=204)
async def answer(
    offer_id: uuid.UUID, body: CriteriaAnswer, account: CurrentAccount, db: DbSession
) -> None:
    """Answer the bonus card. Optional by construction: nothing waits on this call.

    There is no matching dismiss endpoint, and there should not be. Dismissing and
    ignoring are required to be recorded identically, and the row already records both:
    it says ``skip`` from the moment the card was offered, and only this call changes it.
    """
    entry = await db.get(ComparisonLogEntry, offer_id)
    if entry is None or entry.account_id != account.id or entry.kind is not ComparisonKind.criteria:
        raise ApiError(404, "no_such_offer", "That question is not one you were asked.")
    if entry.verdict is not OFFERED:
        # Nothing is overwritten: a second answer would erase the first, and the log
        # keeps every judgment the owner has made. Changing their mind is a later offer.
        raise ApiError(409, "already_answered", "You have already answered this question.")
    entry.verdict = ComparisonVerdict(body.verdict)
    await db.commit()
