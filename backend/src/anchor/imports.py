"""The seed import: the export goes up, and the account comes out with a library.

The one-time import of a Letterboxd export, from upload to the residue the owner is
left resolving by hand. Four things shape it:

*Rows apply as they resolve, not in one batch at the end.* An auto-accepted row lands in
the account the moment the matcher is sure of it, so the library is there while the
review queue is still waiting; an unmatched row affects nothing until the owner binds or
dismisses it. There is no "finish import" step, because there is nothing to finish.

*Matching runs off the request path.* Six hundred rows is six hundred TMDB searches, so
the upload records the rows and hands them to a job. The upload's own answer is what has
been read, not what has been found.

*Re-import is a hard reset.* There is no merge path, ever. Importing again wipes the
account realm - ordering, comparison log, anchors, taste profile, backlog including
hand-added films, watch history - and rebuilds from the new export alone, behind a
warning that enumerates what will go and a type-to-confirm once the comparison log is
worth more than the enumeration alone conveys.

*The rescue is per row and never bulk.* The boxd.it scrape is offered as a button beside
one review row at a time, rate limited, and expected to fail.
"""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from anchor import jobs, letterboxd, matching, seeding
from anchor.accounts import CurrentAccount
from anchor.catalog import FilmCard
from anchor.deps import AppJobs, AppLetterboxd, AppSettings, AppTmdb, DbSession
from anchor.errors import ApiError
from anchor.letterboxd import ExportRow, NotAnExport, RescueFailed
from anchor.matching import Candidate
from anchor.models import (
    Account,
    Import,
    ImportRow,
    ImportRowKind,
    ImportRowState,
    ImportStatus,
)
from anchor.ratelimit import limited

router = APIRouter(prefix="/api/import")

RESOLVED = (ImportRowState.auto_matched, ImportRowState.bound)
"""The two states that mean a row has a film: the matcher decided, or the owner did."""


# --- Wire shapes ---


class ImportCounts(BaseModel):
    """How many lines of each kind the export held, whatever became of them."""

    rating: int = 0
    watchlist: int = 0
    watched: int = 0
    diary: int = 0
    profile_favorite: int = 0


class ImportState(BaseModel):
    """The import area's whole reading: what was read, and what is left to resolve."""

    status: Literal["none", "matching", "complete"]
    source_name: str | None
    created_at: datetime | None
    counts: ImportCounts
    matched: int
    review_pending: int
    unmatched: int
    dismissed: int


class ReviewRow(BaseModel):
    """One line the matcher could not settle, and the films it might be.

    The candidates are ranked by popularity, which is the only ordering TMDB's search
    offers that means anything: the owner is looking for the film they have heard of.
    """

    id: uuid.UUID
    kind: ImportRowKind
    name: str
    year: int | None
    rating: float | None
    rescuable: bool
    """The row carries a boxd.it link, so the per-row Letterboxd rescue can be offered."""
    candidates: list[Candidate]


class ReviewQueue(BaseModel):
    rows: list[ReviewRow]


class UnmatchedRow(BaseModel):
    """A line that found nothing. It affects nothing and waits indefinitely."""

    id: uuid.UUID
    kind: ImportRowKind
    name: str
    year: int | None
    rating: float | None
    rescuable: bool


class UnmatchedList(BaseModel):
    rows: list[UnmatchedRow]


class Binding(BaseModel):
    tmdb_id: int


class Bound(BaseModel):
    """What the binding did, so the screen can drop the row and say what it became."""

    row_id: uuid.UUID
    film: FilmCard


class ResetWarning(BaseModel):
    """Concretely what a re-import destroys, counted rather than described.

    "This erases 50 ratings, 200 comparisons, 3 anchors" is something the owner can
    weigh; "this erases your data" is not.
    """

    rated_films: int
    comparisons: int
    anchors: int
    backlog_films: int
    watch_events: int
    confirmation_required: bool
    """The comparison log is worth more than the counts alone convey, so make them type."""
    confirmation_phrase: str


# --- The import ---


@router.post("", status_code=202)
async def upload(
    request: Request,
    account: CurrentAccount,
    db: DbSession,
    queue: AppJobs,
    settings: AppSettings,
    name: Annotated[str, Query(min_length=1, max_length=255)],
    confirm: Annotated[str | None, Query(max_length=100)] = None,
) -> ImportState:
    """Take an export, record every line that matters, and hand the rest to the matcher.

    A second import is a hard reset: the account realm goes first and the new export
    rebuilds it alone. The warning that got the owner here is a read of its own, so all
    that is left to check at the door is that they typed what it asked for.
    """
    rows = _parsed(await _uploaded(request, settings.import_max_upload_bytes))

    existing = await _import_of(db, account.id)
    if existing is not None:
        await _check_confirmed(db, account, settings, confirm)
        await seeding.wipe_realm(db, account.id)

    record = Import(account_id=account.id, source_name=name, status=ImportStatus.matching)
    db.add(record)
    await db.flush()
    db.add_all([_row(account.id, record.id, row) for row in rows])
    await jobs.enqueue(
        db, queue, jobs.match_import_rows, lock=str(account.id), import_id=str(record.id)
    )
    await db.commit()
    return await _state(db, account.id)


@router.get("")
async def state(account: CurrentAccount, db: DbSession) -> ImportState:
    return await _state(db, account.id)


@router.get("/warning")
async def warning(account: CurrentAccount, db: DbSession, settings: AppSettings) -> ResetWarning:
    """What re-importing would destroy, counted from the account as it stands."""
    return await _warning(db, account.id, settings.import_reset_confirm_comparisons)


@router.get("/review")
async def review(
    account: CurrentAccount, db: DbSession, tmdb: AppTmdb, settings: AppSettings
) -> ReviewQueue:
    """The rows the matcher would not settle, each with the films it thinks they may be."""
    rows = list(await db.scalars(_rows_in(account.id, ImportRowState.review_pending)))
    cards = await matching.candidate_cards(
        db, tmdb, settings, [film_id for row in rows for film_id in row.candidates]
    )
    return ReviewQueue(
        rows=[
            ReviewRow(
                id=row.id,
                kind=row.kind,
                name=row.name,
                year=row.year,
                rating=row.rating,
                rescuable=row.letterboxd_uri is not None,
                candidates=[cards[film_id] for film_id in row.candidates if film_id in cards],
            )
            for row in rows
        ]
    )


@router.get("/unmatched")
async def unmatched(account: CurrentAccount, db: DbSession) -> UnmatchedList:
    """The rows that found nothing. Open indefinitely, and affecting nothing meanwhile."""
    rows = await db.scalars(_rows_in(account.id, ImportRowState.unmatched_open))
    return UnmatchedList(
        rows=[
            UnmatchedRow(
                id=row.id,
                kind=row.kind,
                name=row.name,
                year=row.year,
                rating=row.rating,
                rescuable=row.letterboxd_uri is not None,
            )
            for row in rows
        ]
    )


@router.post("/rows/{row_id}/film")
async def bind(
    row_id: uuid.UUID,
    body: Binding,
    account: CurrentAccount,
    db: DbSession,
    queue: AppJobs,
    tmdb: AppTmdb,
    settings: AppSettings,
) -> Bound:
    """The owner says which film this line is: from the review screen or a manual search."""
    row = await _resolvable(db, account, row_id)
    return await _apply(db, queue, account, tmdb, settings, row, body.tmdb_id)


@router.post(
    "/rows/{row_id}/letterboxd",
    dependencies=[limited("letterboxd", lambda settings: settings.letterboxd_rescue_rate_limit)],
)
async def rescue(
    row_id: uuid.UUID,
    account: CurrentAccount,
    db: DbSession,
    queue: AppJobs,
    tmdb: AppTmdb,
    site: AppLetterboxd,
    settings: AppSettings,
) -> Bound:
    """Follow this one row's boxd.it link and read the TMDB id off the film page.

    One row, one request, rate limited: this is a page scrape of markup Letterboxd never
    promised, so it is a button the owner presses when a row is worth the trouble and
    never something the pipeline leans on. A failure leaves the row exactly as it was.
    """
    row = await _resolvable(db, account, row_id)
    if row.letterboxd_uri is None:
        raise ApiError(409, "no_letterboxd_link", "That row carries no Letterboxd link.")
    try:
        tmdb_id = await site.tmdb_id(row.letterboxd_uri)
    except RescueFailed as error:
        raise ApiError(
            502,
            "letterboxd_unresolved",
            "Letterboxd did not give up a film for that row; try searching instead.",
        ) from error
    return await _apply(db, queue, account, tmdb, settings, row, tmdb_id)


@router.delete("/rows/{row_id}", status_code=204)
async def dismiss(row_id: uuid.UUID, account: CurrentAccount, db: DbSession) -> None:
    """Give up on a row for good. It stops being offered and stays as a record."""
    row = await _resolvable(db, account, row_id)
    row.state = ImportRowState.dismissed
    await db.commit()


# --- Helpers ---


async def _uploaded(request: Request, limit: int) -> bytes:
    """The request body, refused as soon as it goes over rather than after it lands."""
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise ApiError(413, "export_too_large", "That export is larger than Anchor accepts.")
        chunks.append(chunk)
    return b"".join(chunks)


def _parsed(data: bytes) -> list[ExportRow]:
    try:
        return letterboxd.read_export(data)
    except NotAnExport as error:
        raise ApiError(422, "not_an_export", str(error)) from error


def _row(account_id: uuid.UUID, import_id: uuid.UUID, row: ExportRow) -> ImportRow:
    return ImportRow(
        account_id=account_id,
        import_id=import_id,
        kind=row.kind,
        name=row.name,
        year=row.year,
        letterboxd_uri=row.uri,
        rating=row.rating,
        occurred_at=row.occurred_at,
        rewatch=row.rewatch,
        state=ImportRowState.pending,
    )


async def _apply(
    db: AsyncSession,
    queue: AppJobs,
    account: Account,
    tmdb: AppTmdb,
    settings: AppSettings,
    row: ImportRow,
    tmdb_id: int,
) -> Bound:
    """Bind a row to a film at the owner's word, and let it take effect straight away."""
    film = await seeding.apply(db, account.id, row, tmdb_id, tmdb, settings)
    row.state = ImportRowState.bound
    # A bound rating changes the ordering, so the taste profile is behind until it retrains.
    await jobs.schedule_retrain(db, queue, account.id)
    await db.commit()
    return Bound(row_id=row.id, film=FilmCard.of(film))


async def _resolvable(db: AsyncSession, account: Account, row_id: uuid.UUID) -> ImportRow:
    """A row of this account's import still open to an answer."""
    row = await db.scalar(
        select(ImportRow).where(ImportRow.id == row_id, ImportRow.account_id == account.id)
    )
    if row is None:
        raise ApiError(404, "row_not_found", "That import row does not exist.")
    if row.state in RESOLVED:
        raise ApiError(409, "row_already_bound", "That row is already bound to a film.")
    if row.state is ImportRowState.dismissed:
        raise ApiError(409, "row_dismissed", "That row was dismissed.")
    return row


def _rows_in(account_id: uuid.UUID, state: ImportRowState) -> Select[tuple[ImportRow]]:
    return (
        select(ImportRow)
        .where(ImportRow.account_id == account_id, ImportRow.state == state)
        .order_by(ImportRow.kind, ImportRow.name, ImportRow.id)
    )


async def _import_of(db: AsyncSession, account_id: uuid.UUID) -> Import | None:
    record: Import | None = await db.scalar(select(Import).where(Import.account_id == account_id))
    return record


async def _state(db: AsyncSession, account_id: uuid.UUID) -> ImportState:
    record = await _import_of(db, account_id)
    if record is None:
        return ImportState(
            status="none",
            source_name=None,
            created_at=None,
            counts=ImportCounts(),
            matched=0,
            review_pending=0,
            unmatched=0,
            dismissed=0,
        )
    kinds = await _counted(db, account_id, ImportRow.kind)
    states = await _counted(db, account_id, ImportRow.state)
    return ImportState(
        status=record.status.value,
        source_name=record.source_name,
        created_at=record.created_at,
        counts=ImportCounts(**{kind.value: count for kind, count in kinds.items()}),
        matched=sum(states.get(state, 0) for state in RESOLVED),
        review_pending=states.get(ImportRowState.review_pending, 0),
        unmatched=states.get(ImportRowState.unmatched_open, 0),
        dismissed=states.get(ImportRowState.dismissed, 0),
    )


async def _counted(
    db: AsyncSession, account_id: uuid.UUID, column: InstrumentedAttribute[Any]
) -> dict[Any, int]:
    """One ``GROUP BY`` per column rather than a query per value the column can hold."""
    rows = await db.execute(
        select(column, func.count()).where(ImportRow.account_id == account_id).group_by(column)
    )
    return {value: count for value, count in rows}


async def _warning(db: AsyncSession, account_id: uuid.UUID, threshold: int) -> ResetWarning:
    counts = await seeding.realm_counts(db, account_id)
    return ResetWarning(
        rated_films=counts.rated_films,
        comparisons=counts.comparisons,
        anchors=counts.anchors,
        backlog_films=counts.backlog_films,
        watch_events=counts.watch_events,
        confirmation_required=counts.comparisons > threshold,
        confirmation_phrase=CONFIRMATION_PHRASE,
    )


CONFIRMATION_PHRASE = "erase everything"
"""What the owner types to re-import over a comparison log worth protecting."""


async def _check_confirmed(
    db: AsyncSession, account: Account, settings: AppSettings, confirm: str | None
) -> None:
    required = await _warning(db, account.id, settings.import_reset_confirm_comparisons)
    if not required.confirmation_required:
        return
    if (confirm or "").strip().casefold() != CONFIRMATION_PHRASE:
        raise ApiError(
            409,
            "confirmation_required",
            f'Type "{CONFIRMATION_PHRASE}" to replace everything this account holds.',
        )
