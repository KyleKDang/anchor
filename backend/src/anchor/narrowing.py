"""Narrowing a range: which film to ask about next, and what each answer proves.

An owner unsure between two or three adjacent bands selects them on the picker, and
comparisons narrow the range to one band. Each question sets the film against one film
standing for a band in the range - better, worse, about the same, or skip - and the rule
for which film is asked is the question most likely to end the search (rating-system.md).

*Every answer bounds the film and decides nothing else.* Better than a film of band B
means at least B, worse means at most B, about the same means B and ends the search.
That is all a band comparison ever does: it never moves an anchor, never moves another
film, and never re-ranks anything already on the wall.

*An anchor bounds, it never floors.* An anchor sits wherever it sits in its band, so
losing to every 5.0 anchor leaves a low 5.0 exactly as possible as a high 4.5. That is
why two bands cannot be separated by anchors alone: when the film loses to the upper
band's weakest anchor and beats the lower band's strongest it sits at the seam, and only
the boundary question - the bottom film of the upper band against the top film of the
lower - can settle it.

*This module is pure, and the narrowing keeps no state.* A rating in progress needs no
entity (data-model.md), so the whole of a narrowing is replayed from the range the owner
selected and the verdicts they have given: the same inputs always reach the same
question. That is what lets the screen carry the transcript and the server stay a read -
and it means a client cannot name its own opponent, because it never names one at all.
"""

import enum
import uuid
import zlib
from collections.abc import Sequence
from dataclasses import dataclass

from anchor.ordering import Ordering


class Verdict(enum.StrEnum):
    """The four answers a band comparison takes, as the owner meets them on screen."""

    better = "better"
    """At least the band the opponent stood for."""
    worse = "worse"
    """At most that band."""
    same = "same"
    """Exactly that band, and the search ends here."""
    skip = "skip"
    """Records nothing and swaps in the band's next candidate."""


class Phase(enum.Enum):
    """Which question a narrowing is on, which is what fixes the opponent rule.

    Not owner-facing and never stored: it is recomputed with the rest of the narrowing
    every time, and exists so the three opponent rules of rating-system.md have one name
    each rather than being spelled out at each branch.
    """

    middle = enum.auto()
    """Three bands in the range: an anchor of the middle band, nearest the middle of its
    pool, because either direction drops a band."""
    upper = enum.auto()
    """Two bands: the weakest anchor of the upper band, since beating it settles it."""
    lower = enum.auto()
    """Two bands: the strongest anchor of the lower band, since losing to it settles it."""
    boundary = enum.auto()
    """Anchors have bounded the film to the seam, and only the two seam films can settle it."""


@dataclass(frozen=True)
class Opponent:
    """One film standing for a band in a comparison."""

    film_id: int
    band: float
    """The band it stands for, which is what the question is about."""
    anchor: bool
    """An anchor of that band, or its stand-in: the film nearest the boundary in question."""


@dataclass(frozen=True)
class Seam:
    """The boundary question's pair: the bottom film of a band and the top film of the next."""

    upper: int
    upper_band: float
    lower: int
    lower_band: float


@dataclass(frozen=True)
class Narrowing:
    """Where a narrowing stands: the range still live, and the one thing to do about it.

    Exactly one of ``question``, ``seam``, ``choose`` and ``settled`` is set. They are the
    four ways a range ends up somewhere: ask a comparison, ask the boundary question, hand
    the owner what is left because nothing can be asked, or name the band it landed on.
    """

    bands: tuple[float, ...]
    """The bands still in the range, best first."""
    answered: tuple[tuple[Opponent, Verdict], ...]
    """Every comparison replayed, in the order it was answered. The transcript's meaning."""
    question: Opponent | None = None
    seam: Seam | None = None
    choose: bool = False
    """Nothing is left to ask, so the owner picks from what remains of the range."""
    settled: float | None = None
    """The band the answers proved, ready to land."""

    def beaten(self) -> tuple[int, ...]:
        """The films the owner said this one is better than."""
        return tuple(one.film_id for one, verdict in self.answered if verdict is Verdict.better)

    def lost_to(self) -> tuple[int, ...]:
        """The films the owner said this one is worse than."""
        return tuple(one.film_id for one, verdict in self.answered if verdict is Verdict.worse)


def seed_for(account_id: uuid.UUID, subject: int) -> int:
    """The tie-break seed for one film's narrowing: per account, per film, and stable.

    Opponent choice is advisory (ADR 0001), and the only thing left to chance in it is
    which of two equally central anchors gets asked. Fixing that per account and film
    rather than drawing it fresh keeps a narrowing coherent across its own steps - the
    same range replays to the same questions - and keeps two owners from being asked in
    lockstep. Callers pass a seed rather than reading one so the rule can be tested
    against several without inventing an account per seed.
    """
    return zlib.crc32(account_id.bytes) ^ subject


def narrow(
    ordering: Ordering,
    subject: int,
    selected: Sequence[float],
    verdicts: Sequence[Verdict],
    seed: int,
) -> Narrowing:
    """Replay a narrowing from the range the owner selected and the answers they gave.

    The loop is the spec read straight through: pick the question the current range calls
    for, apply the next verdict to it, and stop the moment the range is one band, the
    owner says "about the same", or the transcript runs out and there is something to ask.

    ``subject`` is excluded from every opponent and exemplar. On a re-rate the film being
    rated is already on the wall, and asking the owner whether a film is better than
    itself is the one question that cannot mean anything.
    """
    live = tuple(selected)
    phase = Phase.middle if len(live) == 3 else Phase.upper
    asked: set[int] = set()
    answered: list[tuple[Opponent, Verdict]] = []
    index = 0

    while True:
        if len(live) == 1:
            return Narrowing(bands=live, answered=tuple(answered), settled=live[0])
        if phase is Phase.boundary:
            seam = _seam(ordering, live, subject)
            if seam is None:
                # A seam needs a film either side of it. Without one there is nothing
                # left to ask, and what remains of the range is the owner's to pick from.
                return Narrowing(bands=live, answered=tuple(answered), choose=True)
            return Narrowing(bands=live, answered=tuple(answered), seam=seam)

        band = _target(live, phase)
        candidate = next(
            (
                one
                for one in _candidates(ordering, band, phase, subject, seed)
                if one.film_id not in asked
            ),
            None,
        )
        if candidate is None:
            # A band with nothing left to ask about is passed over rather than dwelt on:
            # a band with no film at all cannot be asked about at all (rating-system.md).
            phase = _advance(phase)
            continue
        if index >= len(verdicts):
            return Narrowing(bands=live, answered=tuple(answered), question=candidate)

        verdict = verdicts[index]
        index += 1
        asked.add(candidate.film_id)
        answered.append((candidate, verdict))
        if verdict is Verdict.skip:
            # Records nothing and swaps in the band's next candidate: same phase, same
            # range, one fewer film to ask about.
            continue
        if verdict is Verdict.same:
            return Narrowing(bands=(band,), answered=tuple(answered), settled=band)
        live = (
            tuple(one for one in live if one >= band)
            if verdict is Verdict.better
            else tuple(one for one in live if one <= band)
        )
        phase = _advance(phase)


def landing_rank(
    ordering: Ordering, subject: int, band: float, rank: int, narrowing: Narrowing
) -> int:
    """The rank clipped to what the comparisons proved: above what it beat, below what it lost to.

    ``rank`` is where the film would go if nothing had been asked - the rank the default
    order gives it, or the rank a re-rate would have kept - and the clip only ever moves
    it far enough that the landing does not contradict an answer the owner has just
    given. Films of other bands say nothing about a rank inside this one, so only the
    answers about this band's own films bite.

    Counted in insertion ranks against the band *without* the subject, so a first
    placement and a re-rate into the film's own band are the same arithmetic: rank ``k``
    means "sits above whoever holds ``k`` now".
    """
    row = [placed.film_id for placed in ordering.row(band) if placed.film_id != subject]
    seats = {film_id: seat for seat, film_id in enumerate(row, start=1)}
    beaten = [seats[film_id] for film_id in narrowing.beaten() if film_id in seats]
    lost_to = [seats[film_id] for film_id in narrowing.lost_to() if film_id in seats]

    # Below every film it lost to first, then above every film it beat, so that a
    # transcript that contradicts itself - possible only if the owner answered two ways
    # about one band - still lands somewhere rather than throwing.
    if lost_to:
        rank = max(rank, max(lost_to) + 1)
    if beaten:
        rank = min(rank, min(beaten))
    return max(1, min(rank, len(row) + 1))


def beside(ordering: Ordering, subject: int, seam: Seam, closer: int) -> tuple[float, int]:
    """The band and rank that put a film beside the exemplar it was judged closer to.

    The boundary question is asked at a seam, so the answer says which side of it the
    film falls on and the exemplar is the film it lands against: closer to the upper
    band's bottom film makes the film that band's new bottom, closer to the lower band's
    top film makes it that band's new top. Either way it comes to rest touching the film
    the owner just measured it against.
    """
    if closer == seam.upper:
        row = [one.film_id for one in ordering.row(seam.upper_band) if one.film_id != subject]
        return seam.upper_band, len(row) + 1
    return seam.lower_band, 1


# --- The opponent rules ---


def _target(live: tuple[float, ...], phase: Phase) -> float:
    """The band this phase asks about."""
    if phase is Phase.middle:
        return live[1]
    return live[0] if phase is Phase.upper else live[-1]


def _advance(phase: Phase) -> Phase:
    """The next question a range gets, once this one is answered or has nothing to ask."""
    return {
        Phase.middle: Phase.upper,
        Phase.upper: Phase.lower,
        Phase.lower: Phase.boundary,
        Phase.boundary: Phase.boundary,
    }[phase]


def _candidates(
    ordering: Ordering, band: float, phase: Phase, subject: int, seed: int
) -> list[Opponent]:
    """Every film that could stand for this band in this phase, best candidate first.

    Anchors first, in the order the phase's rule wants them, and the stand-in last: it
    stands for a band with no anchor *left* to ask about, so it is reached when the pool
    is empty and when every anchor in it has been skipped alike (CONTEXT.md).
    """
    row = [placed.film_id for placed in ordering.row(band) if placed.film_id != subject]
    pool = [
        placed.film_id
        for placed in ordering.row(band)
        if placed.anchored and placed.film_id != subject
    ]
    if phase is Phase.middle:
        ordered = [pool[index] for index in _from_middle(len(pool), seed)]
        stand_in = row[_from_middle(len(row), seed)[0]] if row else None
    elif phase is Phase.upper:
        # The weakest anchor of the upper band, since beating it settles the upper band;
        # and the film nearest the seam below is that band's bottom film.
        ordered = list(reversed(pool))
        stand_in = row[-1] if row else None
    else:
        ordered = list(pool)
        stand_in = row[0] if row else None

    candidates = [Opponent(film_id=film_id, band=band, anchor=True) for film_id in ordered]
    if stand_in is not None and stand_in not in ordered:
        candidates.append(Opponent(film_id=stand_in, band=band, anchor=False))
    return candidates


def _from_middle(size: int, seed: int) -> list[int]:
    """Indices ordered by nearness to the middle, the seed breaking an exact tie.

    An even-sized pool has two equally central films and no rule that prefers either, so
    the seed picks a side and keeps picking it for as long as this narrowing runs. It is
    the only thing about opponent choice that is not fully determined by the spec, which
    is why it is the only thing the seed touches.
    """
    middle = (size - 1) / 2
    upper_first = seed % 2 == 0
    return sorted(
        range(size),
        key=lambda index: (
            abs(index - middle),
            (index > middle) if upper_first else index < middle,
        ),
    )


def _seam(ordering: Ordering, live: tuple[float, ...], subject: int) -> Seam | None:
    """The two films the boundary question shows, or None where there is no seam to show.

    A seam is a boundary between exactly two bands, and it needs a film on each side of
    it. A range still holding three bands has no single seam, and a band holding no film
    has no edge to stand at.
    """
    if len(live) != 2:
        return None
    upper_row = [one.film_id for one in ordering.row(live[0]) if one.film_id != subject]
    lower_row = [one.film_id for one in ordering.row(live[1]) if one.film_id != subject]
    if not upper_row or not lower_row:
        return None
    return Seam(upper=upper_row[-1], upper_band=live[0], lower=lower_row[0], lower_band=live[1])
