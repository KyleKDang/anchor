"""What a bound import row does to the account realm, and what a re-import undoes.

*A rating is a band, and a band is a row.* Letterboxd's ten half-star values map 1:1 onto
Anchor's bands, so every rated row lands in its band the moment it is matched, rated and
final. Nothing about an imported film is provisional and nothing waits to be settled: the
wall is complete when matching completes, and the owner reorders it as much or as little
as they like (onboarding-and-import.md).

*Within a band the rows take the default order.* TMDB's average shrunk toward the catalog
mean, best first, title as the tiebreak - the same rule that seats every newly rated film,
so there is one rule to know. Seeding row by row reproduces it, because each row is placed
against the films already there by the same key that would have sorted them all at once.

*The realm wipe is the one exception to the log's never-deleted rule.* :data:`WIPED` is
the whole of it, declared rather than discovered, so a table added by a later ticket
fails the test that checks this list covers every account-owned table.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from anchor import catalog
from anchor import ordering as ordering_module
from anchor.models import (
    AccountFilm,
    ComparisonContext,
    ComparisonKind,
    ComparisonLogEntry,
    Film,
    ImportRow,
    ImportRowKind,
    LifecycleState,
    Placement,
    WatchEvent,
    WatchOrigin,
    WatchStanding,
)
from anchor.settings import Settings
from anchor.tmdb import Tmdb

WIPED = (
    # Children before parents.
    "tier_states",
    # The reset re-locks both readiness unlocks until evidence re-accumulates, so their
    # dots go with everything else: an account that has already seen one would otherwise
    # cross the bar a second time in silence.
    "unlock_marks",
    "exemplars",
    "weight_vectors",
    "taste_metrics",
    # The prose describes the ordering the reset is deleting, so it cannot outlive it: an
    # owner who has just replaced their library should not find Profile still telling them
    # what they liked about the old one. Its version number goes too, and has to - the
    # number keys discovery's cached verdicts, so restarting the taste means restarting
    # the count that says which taste a cached verdict was about.
    "prose_profile_versions",
    # The anchor marks live on the placements, so they go when those do.
    "placements",
    "comparison_log_entries",
    "watch_events",
    "import_rows",
    "imports",
    "account_films",
)
"""Every account-owned table a re-import empties: the whole account realm."""

KEPT = (
    "auth_sessions",
    "quality_list_entries",
    "profile_constraints",
    "spend_ledger_entries",
    "warmup_progress",
)
"""Account-owned but not the account's film data.

Wiping the sessions would log the owner out mid-import. Wiping the quality list would
throw away the built-in dozen with nothing to re-seed it - seeding happens once, at
account creation - and take the owner's custom qualities with it, which are statements
about their taste rather than anything the export produced. Profile constraints are the
same kind of thing and kept for the same reason, only more so: they are what the owner
said about themselves in their own words, and losing a correction because a CSV was
re-uploaded is exactly what storing them structurally was meant to prevent.

The spend ledger is kept for a different reason entirely. It is not a statement about
taste at all - it is what this month has already cost - and wiping it would make
re-importing a way to reset the monthly caps, which is the one hole the caps exist to
close (architecture.md).

The warmup's marks record which questions the owner has already been asked, and the
import itself is one of the answers, so wiping them would send an owner who took the
import branch straight back to the fork they had just answered - and nothing in them is
account data either, because everything the warmup shows but a skip is derived from
tables that *are* wiped, and comes back rebuilt from the new export.
"""


@dataclass(frozen=True)
class RealmCounts:
    """What a re-import would destroy, counted so the warning can name it concretely."""

    rated_films: int
    judgments: int
    """Every row of the comparison log: the picks and the criteria answers alike.

    "Answers" is the spec's own word for what the warning enumerates
    (onboarding-and-import.md), and under the direct ordering a rating is itself a
    recorded answer - so counting one kind of row would under-state what is about to go.
    """
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
        await _seed_rating(db, account_id, film, row.rating, settings)
    elif row.kind is ImportRowKind.watchlist:
        await _seed_backlog(db, account_id, film.tmdb_id, row)
    elif row.kind in (ImportRowKind.watched, ImportRowKind.diary):
        await _seed_watched(db, account_id, film.tmdb_id)
    if row.kind is ImportRowKind.diary:
        await _seed_watch_event(db, account_id, film.tmdb_id, row)
    return film


async def _seed_rating(
    db: AsyncSession, account_id: uuid.UUID, film: Film, rating: float, settings: Settings
) -> None:
    """Land a rated row in its band at the rank the default order gives it there.

    The rating is the owner's own band pick, so it is logged as one - just an old one -
    and the placement is a placement like any other. Nothing here is provisional and
    nothing waits: the film is rated the moment this returns.

    Seeding row by row reproduces the default order for the whole band, because the rule
    is a fixed key: every row is seated against the films already there by the same
    comparison that would have sorted them all at once.
    """
    account_film = await _account_film(db, account_id, film.tmdb_id)
    if account_film is not None and account_film.state is LifecycleState.rated:
        # Already seeded by an earlier row of this same import - one film can be named
        # twice - and the first row's rating is the one that stands, last synced value
        # included. The import wipes the realm first, so there is no owner-rated film
        # here to skip.
        return
    if account_film is None:
        account_film = AccountFilm(
            account_id=account_id, film_id=film.tmdb_id, state=LifecycleState.backlog
        )
        db.add(account_film)
        await db.flush()

    db.add(
        ComparisonLogEntry(
            account_id=account_id,
            kind=ComparisonKind.band_pick,
            subject_film_id=film.tmdb_id,
            film_a_id=film.tmdb_id,
            film_b_id=None,
            verdict=None,
            band=rating,
            context=ComparisonContext.seed_import,
        )
    )
    order = ordering_module.default_order(settings)
    rank = await ordering_module.default_rank(db, account_id, rating, film, order)
    await ordering_module.land(db, account_film, band=rating, rank=rank)

    # What Letterboxd holds for this film, as far as Anchor knows. It is the import that
    # knows it and nothing else ever will, so it is written here or never. Writing it now
    # is also what makes the sync list empty right after an import: every rated film is
    # already in step with the export it came from.
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

    There is no merge path, ever, so a second import starts from nothing: the ordering
    and its anchor marks, the comparison log, the taste profile, the backlog including
    hand-added films, and the watch history all go, and the new export rebuilds from
    itself alone. The account record and its live sessions are not account data and stay,
    so the owner is still logged in on the other side of it.
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
        judgments=await _count(
            db,
            select(func.count())
            .select_from(ComparisonLogEntry)
            .where(ComparisonLogEntry.account_id == account_id),
        ),
        anchors=await _count(
            db,
            select(func.count())
            .select_from(Placement)
            .where(
                Placement.account_id == account_id,
                Placement.anchored_at.is_not(None),
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
