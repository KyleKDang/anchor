"""The ordering: ten band rows, each a strict order, and how a film gets into one.

The ordering is explicit persisted state (ADR 0001, ADR 0013). Every film's band and
rank are written down on its placement and nothing derives them - not from the
comparison log, not from the advisory math. Every function here that writes runs at the
end of a flow the owner's own act settled, and the account-realm wipe is the only other
thing that touches these rows.

Three ideas carry the module:

*A band is a row, and its rank is dense.* Ranks run 1..n inside a band with no gaps, so
"the film above this one" is a plain subtraction and the wall renders straight off a
sorted read. Landing a film shifts the films it lands above by one, which is one bulk
update per rating - the right trade for a personal library, where ratings are rare and
reads constant.

*The default order is where a film waits for the owner.* Within a band it is TMDB's
average shrunk toward the catalog mean where votes are few, best first, title as the
tiebreak. The shrinkage is the whole point: an obscure film with a handful of perfect
votes must not top a row. The same rule seeds every imported band and seats every newly
rated film, so there is one rule to know, and it is a starting point rather than a
judgment - ``moved_at`` stays empty until the owner actually moves the film.

*The band is the rating.* It is stored because the owner chose it, which is what makes
storing it honest (ADR 0013 supersedes ADR 0002). There is nothing to derive and
nothing that can go stale.
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy import Select, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from anchor.catalog import FilmCard
from anchor.models import BANDS, AccountFilm, Film, LifecycleState, Placement
from anchor.settings import Settings


@dataclass(frozen=True)
class Standing:
    """Where a rated film stands in its band, as both surfaces that say it need it.

    The film page and the picker's done screen make the same statement - "third of your
    4.0s, between these two" - so they read it once, here, rather than each assembling it
    from the ordering and drifting apart on what "neighbour" means.
    """

    band: float
    rank: int
    band_size: int
    anchored: bool
    above: int | None
    below: int | None
    """The films either side of it *in its own band*; None at either end of the row."""

    def named(self) -> list[int]:
        """The neighbour ids that exist, for the one card lookup both surfaces then do."""
        return [film_id for film_id in (self.above, self.below) if film_id is not None]


@dataclass(frozen=True)
class Placed:
    """One rated film as the ordering holds it."""

    film_id: int
    band: float
    rank: int
    """1 is the best film of the band."""
    anchored: bool


@dataclass(frozen=True)
class Ordering:
    """One account's whole ordering: ten band rows, best band first, each in rank order."""

    rows: Mapping[float, tuple[Placed, ...]]
    """Keyed by band; a band with no films is absent rather than empty."""

    def __len__(self) -> int:
        return sum(len(row) for row in self.rows.values())

    def bands(self) -> tuple[float, ...]:
        """The bands holding at least one film, best first."""
        return tuple(band for band in BANDS if self.rows.get(band))

    def row(self, band: float) -> tuple[Placed, ...]:
        return self.rows.get(band, ())

    def of(self, film_id: int) -> Placed | None:
        """Where a film sits, or None where it is not rated."""
        return next(
            (placed for row in self.rows.values() for placed in row if placed.film_id == film_id),
            None,
        )

    def all_film_ids(self) -> list[int]:
        """Every rated film, best band first and best rank first within a band."""
        return [placed.film_id for band in self.bands() for placed in self.rows[band]]

    def anchors(self, band: float) -> tuple[int, ...]:
        """The band's anchor pool, in rank order."""
        return tuple(placed.film_id for placed in self.row(band) if placed.anchored)

    def standing(self, film_id: int) -> Standing | None:
        """Everything a surface says about where one rated film sits, or None if it is not."""
        placed = self.of(film_id)
        if placed is None:
            return None
        above, below = self.neighbours(film_id)
        return Standing(
            band=placed.band,
            rank=placed.rank,
            band_size=len(self.row(placed.band)),
            anchored=placed.anchored,
            above=above,
            below=below,
        )

    def neighbours(self, film_id: int) -> tuple[int | None, int | None]:
        """The films immediately above and below this one *in its own band*.

        Band-local on purpose: the film page shows a rank within a band, so the films
        that rank is against are that band's. An end of a row has no neighbour that way,
        and the honest answer is None rather than the next band's edge, which the owner
        never ranked this film against.
        """
        placed = self.of(film_id)
        if placed is None:
            return None, None
        row = self.row(placed.band)
        index = placed.rank - 1
        above = row[index - 1].film_id if index > 0 else None
        below = row[index + 1].film_id if index + 1 < len(row) else None
        return above, below


async def load(db: AsyncSession, account_id: uuid.UUID) -> Ordering:
    """The account's ordering, in one query. Bands best first, ranks dense from 1."""
    rows = await db.execute(
        select(Placement.band, Placement.rank, Placement.anchored_at, AccountFilm.film_id)
        .join(AccountFilm, AccountFilm.id == Placement.account_film_id)
        .where(Placement.account_id == account_id)
        .order_by(Placement.band.desc(), Placement.rank)
    )
    grouped: dict[float, list[Placed]] = {}
    for band, rank, anchored_at, film_id in rows:
        grouped.setdefault(band, []).append(
            Placed(film_id=film_id, band=band, rank=rank, anchored=anchored_at is not None)
        )
    return Ordering(rows={band: tuple(row) for band, row in grouped.items()})


# --- The default order ---


@dataclass(frozen=True)
class DefaultOrder:
    """The rule that seats an unmoved film: shrunk average, best first, title tiebreak.

    Held as a value rather than computed per film so a whole imported band sorts against
    one catalog mean, and so a test can seat films against a mean it chose.
    """

    catalog_mean: float
    """The typical rating in the catalog, as a fixed prior rather than a live average.

    Fixed on purpose. Anchor's ``films`` table is a sparse mirror of TMDB - only the
    films somebody has touched - so an average over it is dominated by the very films
    being seated, which is exactly when the shrinkage has to bite hardest. A prior that
    moves with its own inputs shrinks nothing.
    """
    prior_votes: int
    """How many votes of the catalog mean a film's own average has to outweigh."""

    def shrunk(self, film: Film) -> float:
        """The film's average pulled toward the catalog mean in proportion to its thinness.

        Deliberately not called a score: CONTEXT.md keeps that word for the recommender's
        own scoring, and this is a sort key over TMDB's numbers, not an opinion of anybody's.

        The standard shrinkage: a film with many votes keeps its own average almost
        entirely, and one with a handful is mostly the catalog speaking. It is what
        stops three perfect votes from topping a row over a film thousands agree on.
        """
        votes = max(film.vote_count, 0)
        return (votes * film.vote_average + self.prior_votes * self.catalog_mean) / (
            votes + self.prior_votes
        )

    def key(self, film: Film) -> tuple[float, str, int]:
        """A film's place in the default order: best score first, then title.

        The id rides last so two films sharing a score and a title still sort the same
        way on every read, which is what makes a seeded band reproducible.
        """
        return (-self.shrunk(film), film.title, film.tmdb_id)

    def sorted(self, films: Sequence[Film]) -> list[Film]:
        return sorted(films, key=self.key)

    def rank_among(self, film: Film, band: Sequence[Film]) -> int:
        """Where the default order puts ``film`` in a band that already holds ``band``.

        The rank the default order gives it, counted against the films actually there:
        a band nobody has touched reads back exactly as :meth:`sorted` left it, and one
        the owner has edited seats the newcomer where the rule says while leaving every
        move they made alone.
        """
        key = self.key(film)
        return 1 + sum(1 for other in band if self.key(other) < key)


def default_order(settings: Settings) -> DefaultOrder:
    """The default order's two constants, read from configuration."""
    return DefaultOrder(
        catalog_mean=settings.default_order_catalog_mean,
        prior_votes=settings.default_order_prior_votes,
    )


async def band_films(db: AsyncSession, account_id: uuid.UUID, band: float) -> list[Film]:
    """The films of one band in rank order: what a landing is seated against."""
    rows = await db.scalars(
        _rated(select(Film), account_id)
        .join(Film, Film.tmdb_id == AccountFilm.film_id)
        .where(Placement.band == band)
        .order_by(Placement.rank)
    )
    return list(rows)


async def default_rank(
    db: AsyncSession, account_id: uuid.UUID, band: float, film: Film, order: DefaultOrder
) -> int:
    """The rank the default order gives a film landing in a band it is not already in."""
    return order.rank_among(film, await band_films(db, account_id, band))


# --- Writing the ordering ---


async def land(db: AsyncSession, account_film: AccountFilm, *, band: float, rank: int) -> Placement:
    """Seat a film at a band and rank, which is what makes it rated.

    The rate-later seat goes with it: the seat is meaningful only while a film is
    watched-unrated, and a rated film is not.
    """
    assert band in BANDS, f"not a half-star band: {band}"
    account_film.state = LifecycleState.rated
    account_film.rate_later = False
    await _open_rank(db, account_film.account_id, band, rank)
    placement = Placement(
        account_id=account_film.account_id,
        account_film_id=account_film.id,
        band=band,
        rank=rank,
    )
    db.add(placement)
    await db.flush()
    return placement


async def re_rate(db: AsyncSession, placement: Placement, *, band: float, rank: int) -> None:
    """Run the picker's answer over a film that was already rated.

    Landing in the same band keeps the film's rank: the owner re-affirmed the rating,
    and where they had put the film inside it was never the question. Landing in another
    takes the rank the default order gives it there and retires the anchor mark, because
    a reference that moved is no longer certain (rating-system.md).

    Either way the placement's clock restarts: "recently rated" is the last placement
    *or re-rate*.
    """
    assert band in BANDS, f"not a half-star band: {band}"
    placement.placed_at = func.now()
    if band == placement.band:
        return
    await _carry(db, placement, band=band, rank=rank)
    await db.flush()


async def shift(db: AsyncSession, placement: Placement, *, rank: int) -> None:
    """Move a film to another rank inside the band it is already in.

    ``rank`` is an insertion rank counted against the band with this film taken out of
    it, which is the same arithmetic a landing uses, so a caller that has clipped a rank
    against the band's other films can hand it straight over.

    Used where a re-rate lands in the film's own band but the comparisons that got it
    there contradict the rank it was holding: a landing may never contradict an answer
    the owner has just given (rating-system.md), and the re-rate's "keeps its rank" is
    about a rating re-affirmed with no question asked.
    """
    if rank == placement.rank:
        return
    await _close_rank(db, placement.account_id, placement.band, placement.rank)
    # Excluded by id because this film is still sitting at its old rank while the gap it
    # is moving into is opened, and a shift that pushed the film it is moving would move
    # it twice.
    await db.execute(
        update(Placement)
        .where(
            Placement.account_id == placement.account_id,
            Placement.band == placement.band,
            Placement.rank >= rank,
            Placement.id != placement.id,
        )
        .values(rank=Placement.rank + 1)
    )
    placement.rank = rank
    await db.flush()


async def move(db: AsyncSession, placement: Placement, *, band: float, rank: int) -> bool:
    """Put a rated film at a rank in a band: the owner's drag on the wall, saved at once.

    ``rank`` is the place the film holds once it has landed - 1..n inside its own band,
    1..n+1 in another - and the films it passes shift by one to keep the band dense. A
    move inside the band changes only ranks. A move across bands changes the rating,
    retires the anchor mark, because a reference that moved is no longer certain, and
    renumbers both rows (rating-system.md, "Moves").

    Returns whether anything moved. Dropping a film where it already sits is not a move:
    nothing is stamped and nothing retrains, so a slip of the pointer costs nothing.

    Raises :class:`RankOffTheEnd` for a rank the band cannot hold.
    """
    assert band in BANDS, f"not a half-star band: {band}"
    within = band == placement.band
    row_size = await _band_size(db, placement.account_id, band)
    last = row_size if within else row_size + 1
    if not 1 <= rank <= last:
        raise RankOffTheEnd(f"rank {rank} is off the end of a band of {row_size}")
    if within:
        if rank == placement.rank:
            return False
        # The rank a film holds once landed is the insertion rank against the band with
        # the film taken out, so the re-rate's shift is this move's shift as well.
        await shift(db, placement, rank=rank)
    else:
        await _carry(db, placement, band=band, rank=rank)
    placement.moved_at = func.now()
    await db.flush()
    return True


class RankOffTheEnd(ValueError):
    """A rank no film of the band can hold: below 1, or past the row's end."""


async def unrate(db: AsyncSession, placement: Placement) -> None:
    """Take a film out of the ordering and close the gap it leaves behind."""
    await _close_rank(db, placement.account_id, placement.band, placement.rank)
    await db.delete(placement)
    await db.flush()


async def _open_rank(db: AsyncSession, account_id: uuid.UUID, band: float, rank: int) -> None:
    """Push every film from ``rank`` down one, so the rank is free to be taken."""
    await db.execute(
        update(Placement)
        .where(
            Placement.account_id == account_id,
            Placement.band == band,
            Placement.rank >= rank,
        )
        .values(rank=Placement.rank + 1)
    )


async def _carry(db: AsyncSession, placement: Placement, *, band: float, rank: int) -> None:
    """Take a film out of its band and seat it in another, retiring the anchor mark.

    Shared by a re-rate and a move because they are the same write once the band
    changes: the old row closes up, the new row opens a slot, and the mark goes, since a
    reference that moved is no longer certain (rating-system.md).
    """
    await _close_rank(db, placement.account_id, placement.band, placement.rank)
    await _open_rank(db, placement.account_id, band, rank)
    placement.band = band
    placement.rank = rank
    placement.anchored_at = None


async def _band_size(db: AsyncSession, account_id: uuid.UUID, band: float) -> int:
    size: int | None = await db.scalar(
        select(func.count())
        .select_from(Placement)
        .where(Placement.account_id == account_id, Placement.band == band)
    )
    return size or 0


async def _close_rank(db: AsyncSession, account_id: uuid.UUID, band: float, rank: int) -> None:
    """Pull every film below ``rank`` up one, so the band stays dense."""
    await db.execute(
        update(Placement)
        .where(
            Placement.account_id == account_id,
            Placement.band == band,
            Placement.rank > rank,
        )
        .values(rank=Placement.rank - 1)
    )


# --- Reads ---


def _rated(query: Select[tuple[Film]], account_id: uuid.UUID) -> Select[tuple[Film]]:
    """One account's placements joined to their films; the left side is named, not guessed."""
    return (
        query.select_from(Placement)
        .join(AccountFilm, AccountFilm.id == Placement.account_film_id)
        .where(Placement.account_id == account_id)
    )


async def placement_of(db: AsyncSession, account_id: uuid.UUID, film_id: int) -> Placement | None:
    """A film's placement, or None where the account has not rated it."""
    placement: Placement | None = await db.scalar(
        select(Placement)
        .join(AccountFilm, AccountFilm.id == Placement.account_film_id)
        .where(Placement.account_id == account_id, AccountFilm.film_id == film_id)
    )
    return placement


async def bands_of(db: AsyncSession, account_id: uuid.UUID) -> dict[int, float]:
    """Every rated film's band. The rating, read straight off the placement."""
    rows = await db.execute(
        select(AccountFilm.film_id, Placement.band)
        .join(AccountFilm, AccountFilm.id == Placement.account_film_id)
        .where(Placement.account_id == account_id)
    )
    return {film_id: band for film_id, band in rows}


async def cards(db: AsyncSession, film_ids: list[int]) -> dict[int, FilmCard]:
    """Film cards for a batch of ids, in one query rather than one per film."""
    if not film_ids:
        return {}
    films = await db.scalars(select(Film).where(Film.tmdb_id.in_(film_ids)))
    return {film.tmdb_id: FilmCard.of(film) for film in films}
