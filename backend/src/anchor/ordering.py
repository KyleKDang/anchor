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

    def score(self, film: Film) -> float:
        """The film's average pulled toward the catalog mean in proportion to its thinness.

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
        return (-self.score(film), film.title, film.tmdb_id)

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
    await _close_rank(db, placement.account_id, placement.band, placement.rank)
    await _open_rank(db, placement.account_id, band, rank)
    placement.band = band
    placement.rank = rank
    placement.anchored_at = None
    await db.flush()


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
