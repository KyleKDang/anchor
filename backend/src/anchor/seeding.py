"""What a bound import row does to the account realm, and what a re-import undoes.

*A rating becomes a band judgment, not a position.* Letterboxd's ten half-star values
map 1:1 onto Anchor's bands, so every imported rating of the same value seeds one
provisional tie-group and no within-band order is ever fabricated. The judgments pin the
dividers, which is what makes the familiar half-stars show the moment the import lands.

*Seeds pin once; live answers move freely.* "Lower weight than live answers" is not a
number anywhere - it is that a seed pins a divider as tightly as its own claim allows
and then stops mattering, so one fresh sliver answer moves a divider hundreds of stale
seeds put there. Weight kept as a running total would be the drifting absolute scale
coming back in through the dividers.

*A seeded slot is found, not remembered.* Which slot holds a band's seeds is read off
the ordering and the dividers each time rather than stored, so a seed bound from the
review screen weeks later lands in the same group as the ones that arrived on day one -
and lands beside the owner's own films rather than through them if the band has since
grown some.

*The realm wipe is the one exception to the log's never-deleted rule.* :data:`WIPED` is
the whole of it, declared rather than discovered, so a table added by a later ticket
fails the test that checks this list covers every account-owned table.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from anchor import bands, catalog
from anchor import ordering as ordering_module
from anchor.bands import Boundaries
from anchor.models import (
    AccountFilm,
    AnchorDesignation,
    AnchorStatus,
    ComparisonContext,
    ComparisonKind,
    ComparisonLogEntry,
    Film,
    ImportRow,
    ImportRowKind,
    LifecycleState,
    PlacementProvenance,
    TieGroupSlot,
    WatchEvent,
    WatchOrigin,
    WatchStanding,
)
from anchor.settings import Settings
from anchor.tmdb import Tmdb

WIPED = (
    # Children before parents, though the two references that would care - a placement's
    # slot and a divider's judgment - are deferred to commit precisely so this wipe can
    # delete both halves in one transaction (data-model.md).
    "tier_states",
    # The tier goes with everything else, unlock dot included: the reset re-locks the
    # ranked tier until evidence re-accumulates (watchlist.md), and an account that has
    # already seen the dot would otherwise cross the bar a second time in silence.
    "exemplars",
    "weight_vectors",
    "taste_metrics",
    "dividers",
    "anchor_designations",
    # Drift is a reading of the ordering, so it cannot outlive the ordering it read:
    # evidence first, since it points at both the flags and the log below it.
    "drift_evidence",
    "drift_flags",
    "placements",
    "tie_group_slots",
    "comparison_log_entries",
    "watch_events",
    "import_rows",
    "imports",
    "account_films",
)
"""Every account-owned table a re-import empties: the whole account realm."""

KEPT = ("auth_sessions",)
"""Account-owned but not account *data*: wiping these would log the owner out mid-import."""


@dataclass(frozen=True)
class RealmCounts:
    """What a re-import would destroy, counted so the warning can name it concretely."""

    rated_films: int
    comparisons: int
    anchors: int
    backlog_films: int
    watch_events: int


# --- Applying one row ---


async def apply(
    db: AsyncSession,
    account_id: uuid.UUID,
    row: ImportRow,
    tmdb_id: int,
    tmdb: Tmdb,
    settings: Settings,
) -> Film:
    """Bind ``row`` to a film and let the binding take effect on the account.

    The film is fetched first and on its own, because filling the shared store commits:
    doing it before anything account-shaped is written keeps a failure here from leaving
    half a row applied.
    """
    film = await catalog.ensure_film(db, tmdb, tmdb_id, settings.film_refresh_days)
    row.film_id = film.tmdb_id

    if row.kind is ImportRowKind.rating and row.rating is not None:
        await _seed_rating(db, account_id, film.tmdb_id, row.rating)
    elif row.kind is ImportRowKind.watchlist:
        await _seed_backlog(db, account_id, film.tmdb_id, row)
    elif row.kind in (ImportRowKind.watched, ImportRowKind.diary):
        await _seed_watched(db, account_id, film.tmdb_id)
    if row.kind is ImportRowKind.diary:
        await _seed_watch_event(db, account_id, film.tmdb_id, row)
    return film


async def _seed_rating(
    db: AsyncSession, account_id: uuid.UUID, film_id: int, rating: float
) -> None:
    """Seat a rated film in its band's provisional tie-group, opening one if none exists.

    The rating is the owner's own band judgment, so it is logged as one and the dividers
    it forces name it: every position a divider has ever held stays auditable back to an
    answer, and a seed is an answer like any other - just an old one.
    """
    account_film = await _account_film(db, account_id, film_id)
    if account_film is not None and account_film.state is LifecycleState.rated:
        # Already seeded by an earlier row of this same import - one film can be named
        # twice - and the first row's rating is the one that stands, last synced value
        # included. The import wipes the realm first, so there is no owner-placed film
        # here to skip.
        return
    if account_film is None:
        account_film = AccountFilm(
            account_id=account_id, film_id=film_id, state=LifecycleState.backlog
        )
        db.add(account_film)
        await db.flush()

    judgment = bands.judgment(
        account_id, film_id=film_id, band=rating, context=ComparisonContext.seed_import
    )
    db.add(judgment)
    await db.flush()

    ordering = await ordering_module.load(db, account_id)
    boundaries = await bands.load(db, account_id)
    joined = await _seeded_slot(db, account_id, ordering, boundaries, rating)
    if joined is not None:
        ordering_module.land(
            db, account_film, slot=joined, provenance=PlacementProvenance.import_seeded
        )
    else:
        index = seat_for(boundaries, rating, len(ordering))
        slot = await ordering_module.new_slot(db, account_id, index, band=rating, judgment=judgment)
        ordering_module.land(
            db, account_film, slot=slot, provenance=PlacementProvenance.import_seeded
        )
        moved = await bands.load(db, account_id)
        await bands.move(db, account_id, moved, bands.pins_for(moved, index, rating), judgment)

    # What Letterboxd holds for this film, as far as Anchor knows. It is the import that
    # knows it and nothing else ever will, so it is written here or never.
    account_film.last_synced_rating = rating
    await db.flush()


async def _seed_backlog(
    db: AsyncSession, account_id: uuid.UUID, film_id: int, row: ImportRow
) -> None:
    """A watchlist row seeds the backlog, unless the owner has already seen the film.

    Rows already rated in the same import are skipped, and so is anything watched: the
    lifecycle states are exclusive, and a film on both lists is one Letterboxd never
    took off the watchlist.
    """
    account_film = await _account_film(db, account_id, film_id)
    if account_film is not None:
        return
    db.add(
        AccountFilm(
            account_id=account_id,
            film_id=film_id,
            state=LifecycleState.backlog,
            # Letterboxd's own added-at date, so the backlog's default sort means
            # something the moment the import lands rather than being one flat instant.
            **({"added_at": row.occurred_at} if row.occurred_at is not None else {}),
        )
    )
    await db.flush()


async def _seed_watched(db: AsyncSession, account_id: uuid.UUID, film_id: int) -> None:
    """A watched row with no rating becomes watched-unrated, with a rate-later seat.

    Outside the ordering, the backlog, and the taste profile: its only effects are that
    discovery will never suggest it and that it holds a seat in the rate-later queue.
    """
    account_film = await _account_film(db, account_id, film_id)
    if account_film is None:
        db.add(
            AccountFilm(
                account_id=account_id,
                film_id=film_id,
                state=LifecycleState.watched_unrated,
                rate_later=True,
            )
        )
        await db.flush()
        return
    if account_film.state is LifecycleState.backlog:
        account_film.state = LifecycleState.watched_unrated
        account_film.rate_later = True


async def _seed_watch_event(
    db: AsyncSession, account_id: uuid.UUID, film_id: int, row: ImportRow
) -> None:
    """A diary row becomes one watch event, and so counts into the account's watch clock.

    No diary UI follows: these are the history the clock is measured in, and every
    cooldown and staleness measure in Anchor is denominated in that count rather than in
    calendar time, so an imported back catalogue starts the clock where the owner is.
    """
    event = WatchEvent(
        account_id=account_id,
        film_id=film_id,
        standing=WatchStanding.plain_backlog,
        origin=WatchOrigin.import_seeded,
        rewatch=row.rewatch,
    )
    if row.occurred_at is not None:
        event.watched_at = row.occurred_at
    db.add(event)
    await db.flush()


# --- Where a seeded slot goes ---


def seat_for(boundaries: Boundaries, band: float, total: int) -> int:
    """The index a slot known to be in ``band`` opens at, given the dividers so far.

    The dividers already pinned fence the band in from both sides, and the fence is
    usually tight enough to leave exactly one index. Where it is not - the band already
    holds films the owner placed themselves - the new slot goes at the bottom of the
    band, which claims the least: it asserts nothing about being better than films it
    was never compared with.
    """
    over = _fence(boundaries, bands.BANDS[: bands.rank(band)], max)
    under = _fence(boundaries, bands.BANDS[bands.rank(band) : -1], min)
    if under is not None:
        return under
    return over if over is not None else total


def _fence(boundaries: Boundaries, keys: tuple[float, ...], edge) -> int | None:  # type: ignore[no-untyped-def]
    pinned = [boundaries[key] for key in keys if key in boundaries]
    return edge(pinned) if pinned else None


async def _seeded_slot(
    db: AsyncSession,
    account_id: uuid.UUID,
    ordering: ordering_module.Ordering,
    boundaries: Boundaries,
    band: float,
) -> TieGroupSlot | None:
    """This band's provisional tie-group, if the import has already opened one.

    A slot counts as the band's seed group only while every film in it is still an
    import seed. Once a comparison has pulled a film out into a definitive slot, that
    slot is a judgment about two particular films, and a later seed joining it would be
    asserting a tie nobody made.
    """
    seeded = await ordering_module.seeded_slot_ids(db, account_id)
    for index, slot in enumerate(ordering.slots):
        if slot.id in seeded and bands.band_of_slot(boundaries, index) == band:
            return await ordering_module.slot_by_id(db, slot.id)
    return None


async def _account_film(
    db: AsyncSession, account_id: uuid.UUID, film_id: int
) -> AccountFilm | None:
    account_film: AccountFilm | None = await db.scalar(
        select(AccountFilm).where(
            AccountFilm.account_id == account_id, AccountFilm.film_id == film_id
        )
    )
    return account_film


# --- The hard reset ---


async def wipe_realm(db: AsyncSession, account_id: uuid.UUID) -> None:
    """Empty the account realm in one transaction: the sole exception to never-deleted.

    There is no merge path, ever, so a second import starts from nothing: the ordering,
    the comparison log, the anchors, the drift flags, the taste profile, the backlog
    including hand-added films, and the watch history all go, and the new export rebuilds
    from itself alone. The account record and its live sessions are not account data and
    stay, so the owner is still logged in on the other side of it.
    """
    for table in WIPED:
        await db.execute(text(f'DELETE FROM "{table}" WHERE account_id = :id'), {"id": account_id})
    await db.flush()


async def realm_counts(db: AsyncSession, account_id: uuid.UUID) -> RealmCounts:
    """What the account holds right now, for the warning to enumerate before destroying it."""
    return RealmCounts(
        rated_films=await _count(
            db,
            select(func.count())
            .select_from(AccountFilm)
            .where(
                AccountFilm.account_id == account_id,
                AccountFilm.state == LifecycleState.rated,
            ),
        ),
        comparisons=await _count(
            db,
            select(func.count())
            .select_from(ComparisonLogEntry)
            .where(
                ComparisonLogEntry.account_id == account_id,
                ComparisonLogEntry.kind == ComparisonKind.overall,
            ),
        ),
        anchors=await _count(
            db,
            select(func.count())
            .select_from(AnchorDesignation)
            .where(
                AnchorDesignation.account_id == account_id,
                AnchorDesignation.status == AnchorStatus.current,
            ),
        ),
        backlog_films=await _count(
            db,
            select(func.count())
            .select_from(AccountFilm)
            .where(
                AccountFilm.account_id == account_id,
                AccountFilm.state == LifecycleState.backlog,
            ),
        ),
        watch_events=await _count(
            db,
            select(func.count()).select_from(WatchEvent).where(WatchEvent.account_id == account_id),
        ),
    )


async def _count(db: AsyncSession, query) -> int:  # type: ignore[no-untyped-def]
    return await db.scalar(query) or 0
