"""Bands and dividers: the structure a half-star rating is derived from.

Ten half-star bands are a fixed vocabulary, not data. What is stored is the nine
*dividers* between them, and a film's rating is simply which dividers its slot sits
between - never a value written down anywhere (data-model.md).

Three ideas carry the whole module:

*A divider is an index into the ordering, and unpinned is the absence of one.* Slots
above a divider are the ones at indices below its ``boundary``; slots from there down
are below it. A divider nobody's judgments have located yet has no row, and every band
that would need it stays underivable - so a film shows its position and no stars, which
is the honest answer rather than a guess (onboarding-and-import.md).

*Renumbering is not moving.* Opening a slot above a divider shifts its index, but the
divider still separates the same two slots, so nothing has been claimed. A divider
*moves* only as the direct consequence of a band judgment, and the judgment it moved
for is recorded on it, so every position it has ever held is auditable (ADR 0002).

*A landing exactly on a divider is the one thing position cannot settle.* Every other
index falls unambiguously between two dividers. That single case is why the sliver
question exists, and why it is asked before the slot is ever opened: the answer is what
decides which side of the divider the new film goes, and so which way the divider moves.
"""

import uuid
from collections.abc import Mapping

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from anchor.models import (
    ComparisonContext,
    ComparisonKind,
    ComparisonLogEntry,
    ComparisonStatus,
    Divider,
)

BANDS: tuple[float, ...] = (5.0, 4.5, 4.0, 3.5, 3.0, 2.5, 2.0, 1.5, 1.0, 0.5)
"""The ten half-star bands, best first. Fixed vocabulary: never rows, never configurable."""

Boundaries = Mapping[float, int]
"""Pinned dividers, keyed by the better of the two bands each one separates."""


def rank(band: float) -> int:
    """How far down the scale a band sits; smaller is better. Also its BANDS index."""
    return BANDS.index(band)


def band_above(band: float) -> float | None:
    """The band one half-star better, or None at the top of the scale."""
    index = rank(band)
    return BANDS[index - 1] if index > 0 else None


def divider_below(band: float) -> float | None:
    """The key of the divider under a band, or None below 0.5 where there is none."""
    return band if band != BANDS[-1] else None


def divider_above(band: float) -> float | None:
    """The key of the divider over a band, or None above 5.0 where there is none."""
    return band_above(band)


# --- Derivation ---


def bands_possible_for_slot(boundaries: Boundaries, index: int) -> tuple[float, ...]:
    """Every band a slot at ``index`` could be in, given the dividers pinned so far.

    A slot is never *on* a divider - dividers sit between slots - so more than one
    answer here means the structure is missing, not that the owner owes an answer.
    """
    return _possible(boundaries, index, inclusive=False)


def bands_possible_for_insertion(boundaries: Boundaries, index: int) -> tuple[float, ...]:
    """Every band a film landing at insertion index ``index`` could be in.

    Inclusive where the slot version is strict, because a landing sitting exactly on a
    divider genuinely could go either side of it: that is the sliver question's whole
    subject, and only the owner can settle it.
    """
    return _possible(boundaries, index, inclusive=True)


def _possible(boundaries: Boundaries, index: int, *, inclusive: bool) -> tuple[float, ...]:
    possible = []
    for band in BANDS:
        over = _lowest_the_divider_over(boundaries, band)
        under = _highest_the_divider_under(boundaries, band)
        if over is not None and over > index:
            continue  # the divider over this band cannot reach above the slot
        if under is not None and (under < index if inclusive else under <= index):
            continue  # the divider under this band cannot reach below it
        possible.append(band)
    return tuple(possible)


def _lowest_the_divider_over(boundaries: Boundaries, band: float) -> int | None:
    """How far down the divider over ``band`` could sit, given the ones already pinned.

    Dividers appear in band order, so an unpinned one is still fenced in by its pinned
    neighbours: the 5.0/4.5 divider cannot sit below the 4.5/4.0 divider, however little
    anyone has said about it. Reading that fence is what keeps a film below the 4.0
    anchor from counting as a possible 5.0 just because the top of the scale is empty.
    """
    pinned = [boundaries[key] for key in BANDS[: rank(band)] if key in boundaries]
    return max(pinned) if pinned else None


def _highest_the_divider_under(boundaries: Boundaries, band: float) -> int | None:
    """How far up the divider under ``band`` could sit, by the same fence, from below."""
    pinned = [boundaries[key] for key in BANDS[rank(band) : -1] if key in boundaries]
    return min(pinned) if pinned else None


def band_of_slot(boundaries: Boundaries, index: int) -> float | None:
    """A slot's band, or None while the dividers that would decide it are unpinned."""
    possible = bands_possible_for_slot(boundaries, index)
    return possible[0] if len(possible) == 1 else None


def undecided_at(boundaries: Boundaries, index: int) -> tuple[float, ...]:
    """The bands a landing at ``index`` sits between, when only the owner can choose.

    Empty when position already decides the band, and empty too when the ambiguity is
    missing structure rather than a boundary landing - there, no answer the owner could
    give would locate a divider nobody has pinned, so the film shows position-only and
    is asked nothing.
    """
    candidates = bands_possible_for_insertion(boundaries, index)
    if len(candidates) < 2:
        return ()
    # The dividers between the candidates are the ones the landing sits on. If any is
    # unpinned, the run is wide because the scale has a hole in it, not because of this
    # film, and a question here would be asking the owner to fill in the hole blind.
    if any(boundaries.get(band) is None for band in candidates[:-1]):
        return ()
    return candidates


# --- Moves and renumbering ---


def after_insert(boundaries: Boundaries, index: int, band: float | None) -> dict[float, int]:
    """Where the dividers sit once a slot opens at ``index`` holding a film in ``band``.

    Renumbering for everything the new slot passes, and one real decision for a divider
    sitting exactly at ``index``: the new slot goes above it if the film's band is that
    divider's better side. A film landing position-only decides nothing and so goes
    below them, which claims the least - its band stays underived either way.
    """
    moved = {}
    for upper, boundary in boundaries.items():
        slot_above = boundary > index or (
            boundary == index and band is not None and rank(band) <= rank(upper)
        )
        moved[upper] = boundary + 1 if slot_above else boundary
    return moved


def after_remove(boundaries: Boundaries, index: int) -> dict[float, int]:
    """Where the dividers sit once the slot at ``index`` is gone. Renumbering only."""
    return {
        upper: boundary - 1 if boundary > index else boundary
        for upper, boundary in boundaries.items()
    }


def sharpened_by(boundaries: Boundaries, index: int) -> tuple[float, ...]:
    """The dividers a landing at ``index`` sits on: the ones its band answer decides."""
    return tuple(upper for upper, boundary in boundaries.items() if boundary == index)


def pins_for(boundaries: Boundaries, index: int, band: float) -> dict[float, int]:
    """The divider positions a judgment of "the slot at ``index`` is ``band``" forces.

    Only the two dividers bracketing the band can be involved, and each is touched only
    where it has to be, landing as tight around the slot as it can. Tight is the least
    the judgment can be read to claim: it says this film is a 4.0, not that anything
    else is. Everything wider comes later, from judgments about those other films.
    """
    pins = {}
    over = divider_above(band)
    if over is not None and ((current := boundaries.get(over)) is None or current > index):
        pins[over] = index
    under = divider_below(band)
    if under is not None and ((current := boundaries.get(under)) is None or current <= index):
        pins[under] = index + 1
    return pins


def in_band_order(boundaries: Boundaries) -> bool:
    """The dividers run best to worst without crossing: a band never starts below its end."""
    pinned = [boundaries[band] for band in BANDS[:-1] if band in boundaries]
    return all(earlier <= later for earlier, later in zip(pinned, pinned[1:], strict=False))


# --- Persistence ---


async def load(db: AsyncSession, account_id: uuid.UUID) -> dict[float, int]:
    """One account's pinned dividers. Unpinned ones are simply absent."""
    rows = await db.execute(
        select(Divider.upper_band, Divider.boundary).where(Divider.account_id == account_id)
    )
    return {upper: boundary for upper, boundary in rows}


async def renumber(db: AsyncSession, account_id: uuid.UUID, boundaries: Boundaries) -> None:
    """Write indices that shifted under the dividers. Not a move: nothing is claimed."""
    rows = await db.scalars(select(Divider).where(Divider.account_id == account_id))
    for divider in rows:
        boundary = boundaries.get(divider.upper_band)
        if boundary is not None:
            divider.boundary = boundary


async def move(
    db: AsyncSession,
    account_id: uuid.UUID,
    boundaries: Boundaries,
    pins: Mapping[float, int],
    judgment: ComparisonLogEntry,
) -> dict[float, int]:
    """Pin or move dividers as a band judgment's direct consequence, and stamp it on them.

    ``judgment`` must already be flushed: a divider that named a judgment which does not
    exist would be a move nobody could audit, which is the one thing this table is for.
    """
    merged = {**boundaries, **pins}
    assert in_band_order(merged), f"a band judgment crossed the dividers: {merged}"
    existing = {
        divider.upper_band: divider
        for divider in await db.scalars(select(Divider).where(Divider.account_id == account_id))
    }
    for upper, boundary in pins.items():
        divider = existing.get(upper)
        if divider is None:
            db.add(
                Divider(
                    account_id=account_id,
                    upper_band=upper,
                    boundary=boundary,
                    pinned_by_id=judgment.id,
                )
            )
        else:
            divider.boundary = boundary
            divider.pinned_by_id = judgment.id
            divider.moved_at = func.now()
    await db.flush()
    return merged


def judgment(
    account_id: uuid.UUID,
    *,
    film_id: int,
    band: float,
    context: ComparisonContext,
    exemplar: int | None = None,
) -> ComparisonLogEntry:
    """One band judgment for the log: "this film is a 4.0", and how it was asked.

    A sliver answer names the exemplar the owner judged the film closest to; a plain
    band pick names none, because it offered bands rather than films. Both assert the
    same thing, and both are what a divider move is auditable to.
    """
    return ComparisonLogEntry(
        account_id=account_id,
        kind=ComparisonKind.sliver if exemplar is not None else ComparisonKind.band,
        subject_film_id=film_id,
        film_a_id=film_id,
        film_b_id=exemplar,
        verdict=None,
        band=band,
        context=context,
        status=ComparisonStatus.active,
    )
