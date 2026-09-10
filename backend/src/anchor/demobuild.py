"""The demo account's fixture build: an authored outcome, replayed through the product.

``demofixture.json`` states the target - the films with their bands and within-band
order, which are anchors, what is pinned, vetoed, rewatched, dismissed, rated later - and
this module gets there the way an owner would: it imports the ratings as a Letterboxd
export, then applies every move, mark and action through the real API, and lets the real
jobs retrain, write the prose and stock the shelf (demo-account.md, "Build and refresh").

*Never raw rows.* A row written by hand silently diverges from what the engine would
have produced, and rots with the next schema change; a replay cannot, because the only
thing it knows how to do is press the product's own buttons. The one privileged write is
the account itself: it is created verified with no password, handed one session for the
build to use, and flagged ``is_demo`` *last*, so #42's enforcement needs no bypass and
the replay is itself the proof that none exists - a flagged account refuses every write
below.

*Staged states are declarative.* "This film sits third in its band" is a move to rank
three; "this rating changed since the import" is an export row at the old value and a
move to the new one, which is also what puts the film on the sync list. Timestamps are
days before build time, so the account reads currently lived-in on every rebuild.

*A rebuild replaces.* The new account is built under a working address beside whatever
demo is live, and swaps in only once it has passed its own checks: the old account goes,
the new one takes the demo's address and flag, and its LLM spend is re-scoped to the
shared ledger so the cost survives the account it was bought for and the demo's own
monthly cap does not reset with every deploy.

*A build that would degrade the demo is refused.* No prose or an empty shelf - a
provider down, no credential - leaves the previous demo standing and exits non-zero.

Run as ``python -m anchor.demobuild`` on every deploy (the ``demo`` compose service). It
boots the app in-process and talks to it over ASGI, exactly as the API suite does, and
waits on the queue for the worker rather than running jobs itself: the worker is the one
process that loads the LLM seam (architecture.md), and the build is a client of it.
"""

import asyncio
import csv
import io
import logging
import sys
import time
import uuid
import zipfile
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from importlib import resources
from typing import Any, Literal, Self

import httpx
import procrastinate
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import delete, update

from anchor import accounts, qualities, sentry
from anchor.db import Database
from anchor.main import create_app
from anchor.models import BANDS, Account, AuthSession, SpendLedgerEntry
from anchor.settings import Settings

log = logging.getLogger(__name__)

DEMO_EMAIL = "demo@anchor.invalid"
"""The demo account's address. ``.invalid`` can never receive mail, so nothing can ever
verify it, and the address is what the signup and login forms refuse (#42)."""

BUILDING_EMAIL = "demo-building@anchor.invalid"
"""Where the next demo is built while the current one keeps serving visitors."""

SESSION_TTL = timedelta(hours=24)
"""The build's own session. Deleted at the swap; this only has to outlast a slow build."""

MIN_FILMS, MAX_FILMS = 60, 80
"""The size ADR 0015 fixed: enough to fill the bands, few enough to choose on purpose."""

Drain = Callable[[uuid.UUID], Awaitable[None]]
"""Waits until every job the account owes has run. The API suite runs them inline; the
process below waits on the worker."""


class BuildFailed(Exception):
    """The replay did not reach the authored outcome; the previous demo is left standing."""


# --- The fixture ---


class FixtureFilm(BaseModel):
    """A film as the fixture names it: enough to import it, and to describe the choice."""

    tmdb_id: int
    title: str
    year: int
    genres: list[str]
    director: str
    language: str = "en"


class WallFilm(FixtureFilm):
    anchor: bool = False
    watched_days_ago: int = Field(ge=0)
    imported_as: float | None = None
    """The export's rating where it differs from the band: the film was moved across bands
    after the import, which is what makes it a sync-list row."""


class BandRow(BaseModel):
    band: float
    films: list[WallFilm] = Field(min_length=1)
    """In rank order: the first film is rank one."""


class BacklogFilm(FixtureFilm):
    added_days_ago: int = Field(ge=0)
    pinned: bool = False
    vetoed: bool = False


class RateLaterFilm(FixtureFilm):
    watched_days_ago: int = Field(ge=0)


class Rewatch(BaseModel):
    tmdb_id: int
    days_ago: int = Field(ge=0)
    answer: Literal["confirmed", "changed", "skip"] | None
    """The still-feel-the-same answer, or None to leave the question open on the page."""


class CriteriaSession(BaseModel):
    tmdb_id: int
    answers: int = Field(ge=1)


class Fixture(BaseModel):
    """The whole authored outcome, validated so a wrong fixture fails before it builds."""

    sensibility: str
    """The taste in one sentence: what ADR 0015 asks be recorded beside the films."""
    edges: list[str]
    """The judgments on famous films a visitor would argue with, written down."""
    wall: list[BandRow]
    backlog: list[BacklogFilm]
    rate_later: list[RateLaterFilm]
    rewatches: list[Rewatch]
    criteria_session: CriteriaSession
    qualities: list[str]
    """Picker selections, by name: the profile constraints the demo shows in play."""
    dismiss_suggestions: int = Field(ge=0)
    """How many of the feed's own weakest suggestions the build turns down, so the
    dismissed list has entries. Which films those are is the engine's to decide."""

    @model_validator(mode="after")
    def _coherent(self) -> Self:
        rated = [film for band in self.wall for film in band.films]
        if not MIN_FILMS <= len(rated) <= MAX_FILMS:
            raise ValueError(f"{len(rated)} rated films; ADR 0015 fixes {MIN_FILMS} to {MAX_FILMS}")
        bands = [band.band for band in self.wall]
        if sorted(bands) != sorted(BANDS):
            raise ValueError("the wall must name each of the ten bands exactly once")
        ids = [film.tmdb_id for film in self.films]
        if len(ids) != len(set(ids)):
            raise ValueError("a film appears twice")
        rated_ids = {film.tmdb_id for film in rated}
        for rewatch in self.rewatches:
            if rewatch.tmdb_id not in rated_ids:
                raise ValueError(f"rewatch of {rewatch.tmdb_id}, which is not on the wall")
        if self.criteria_session.tmdb_id not in rated_ids:
            raise ValueError("the criteria session is about a film that is not on the wall")
        for film in rated:
            if film.imported_as is not None and film.imported_as not in BANDS:
                raise ValueError(f"{film.title} imported as {film.imported_as}, not a band")
        return self

    @property
    def films(self) -> list[FixtureFilm]:
        """Every film the fixture names, whatever state it is in."""
        return [
            *(film for band in self.wall for film in band.films),
            *self.backlog,
            *self.rate_later,
        ]

    @property
    def band_of(self) -> dict[int, float]:
        return {film.tmdb_id: band.band for band in self.wall for film in band.films}

    @classmethod
    def load(cls) -> "Fixture":
        text = resources.files("anchor").joinpath("demofixture.json").read_text("utf-8")
        return cls.model_validate_json(text)


# --- The export the import replays ---


def export(fixture: Fixture, built_at: datetime) -> bytes:
    """The fixture as a Letterboxd export: the four files the importer reads, no profile.

    A rated film is a ratings row and a diary row for its watch, at the rating the export
    held (``imported_as`` where the band has moved since); a rewatch is a second diary
    row flagged as one; a rate-later film is a watched row and a diary row with no
    rating; a backlog film is a watchlist row. Every date is ``built_at`` less the days the
    fixture names, which is the whole of "timestamps relative to build time".
    """
    rewatched = {rewatch.tmdb_id: rewatch for rewatch in fixture.rewatches}
    ratings: list[tuple[str, ...]] = []
    diary: list[tuple[str, ...]] = []
    for band in fixture.wall:
        for film in band.films:
            rating = _stars(film.imported_as if film.imported_as is not None else band.band)
            watched = _day(built_at, film.watched_days_ago)
            ratings.append((watched, film.title, str(film.year), "", rating))
            diary.append((watched, film.title, str(film.year), "", rating, "", "", watched))
            if film.tmdb_id in rewatched:
                again = _day(built_at, rewatched[film.tmdb_id].days_ago)
                diary.append((again, film.title, str(film.year), "", rating, "Yes", "", again))
    watched_rows = []
    for later in fixture.rate_later:
        seen = _day(built_at, later.watched_days_ago)
        watched_rows.append((seen, later.title, str(later.year), ""))
        diary.append((seen, later.title, str(later.year), "", "", "", "", seen))
    watchlist = [
        (_day(built_at, film.added_days_ago), film.title, str(film.year), "")
        for film in fixture.backlog
    ]
    members = {
        "ratings.csv": _csv(("Date", "Name", "Year", "Letterboxd URI", "Rating"), ratings),
        "watchlist.csv": _csv(("Date", "Name", "Year", "Letterboxd URI"), watchlist),
        "watched.csv": _csv(("Date", "Name", "Year", "Letterboxd URI"), watched_rows),
        "diary.csv": _csv(
            ("Date", "Name", "Year", "Letterboxd URI", "Rating", "Rewatch", "Tags", "Watched Date"),
            diary,
        ),
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in members.items():
            archive.writestr(name, body)
    return buffer.getvalue()


def _stars(rating: float) -> str:
    """Whole stars serialise without a decimal, exactly as Letterboxd writes them."""
    return str(int(rating)) if rating == int(rating) else str(rating)


def _day(built_at: datetime, days_ago: int) -> str:
    return (built_at - timedelta(days=days_ago)).date().isoformat()


def _csv(header: tuple[str, ...], rows: Sequence[tuple[str, ...]]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return out.getvalue()


# --- The replay ---


class _Api:
    """The build's hands: the API over one client, on the build's own session.

    Every call names the status it expects, so a refusal anywhere in the replay fails the
    build with the request that was refused rather than with whatever the next step
    tripped over.
    """

    def __init__(self, client: httpx.AsyncClient, token: str) -> None:
        self._client = client
        self._headers = {"Cookie": f"{accounts.SESSION_COOKIE}={token}"}

    async def get(self, path: str, **params: Any) -> Any:
        return await self._call("GET", path, params=params or None)

    async def post(self, path: str, json: Any = None, *, expect: int = 200, **extra: Any) -> Any:
        return await self._call("POST", path, json=json, expect=expect, **extra)

    async def put(self, path: str, json: Any) -> Any:
        return await self._call("PUT", path, json=json)

    async def _call(self, method: str, path: str, *, expect: int = 200, **extra: Any) -> Any:
        response = await self._client.request(method, path, headers=self._headers, **extra)
        if response.status_code != expect:
            raise BuildFailed(
                f"{method} {path} answered {response.status_code}, wanted {expect}: {response.text}"
            )
        return response.json() if response.content else None


async def build(
    client: httpx.AsyncClient, db: Database, fixture: Fixture, *, drain: Drain
) -> uuid.UUID:
    """Build the demo from ``fixture`` and swap it in. Returns the new demo account's id."""
    built_at = datetime.now(UTC)
    account_id, token = await _fresh_account(db, built_at)
    api = _Api(client, token)
    log.info("demo build: replaying %s films for account %s", len(fixture.films), account_id)

    await api.post("/api/warmup/enter")
    await _import(api, fixture, built_at, account_id, drain)
    await _arrange(api, fixture)
    for band in fixture.wall:
        for anchor in (film for film in band.films if film.anchor):
            await api.post(f"/api/anchors/{anchor.tmdb_id}", expect=204)
    await _choose_qualities(api, fixture)
    await _answer_criteria(api, fixture)
    for rewatch in fixture.rewatches:
        if rewatch.answer is not None:
            await api.post(
                f"/api/rewatches/{rewatch.tmdb_id}", {"answer": rewatch.answer}, expect=204
            )
    await drain(account_id)

    # The tier and the shelf are both stocked by a visit, and only by one: arriving is the
    # session boundary that seats the tier and the one spend trigger discovery has.
    await api.get("/api/watchlist/tier")
    for queued in fixture.backlog:
        if queued.pinned:
            await api.post(f"/api/watchlist/{queued.tmdb_id}/pin", expect=204)
        if queued.vetoed:
            await api.post(f"/api/watchlist/{queued.tmdb_id}/veto", expect=204)
    await api.get("/api/discovery")
    await drain(account_id)
    # The restock buys verdicts and writes nothing to the shelf; the shelf is derived from
    # them at the next arrival, so the demo arrives once more before anybody else does.
    await api.get("/api/discovery")
    await _turn_down_suggestions(api, fixture)
    await api.post("/api/warmup/dismiss")
    await drain(account_id)

    await _check(api, fixture)
    await _swap_in(db, account_id)
    log.info("demo build: account %s is now the demo", account_id)
    return account_id


async def _fresh_account(db: Database, now: datetime) -> tuple[uuid.UUID, str]:
    """The one privileged write: an account with no password, verified, with a session.

    Any half-built account a previous run left behind goes first. Seeding the quality
    list here is what verification does for a real account (:mod:`anchor.accounts`),
    since this is the moment the account is allowed its first rows.
    """
    async with db.sessions() as session:
        await session.execute(delete(Account).where(Account.email == BUILDING_EMAIL))
        account = Account(email=BUILDING_EMAIL, verified_at=now)
        session.add(account)
        await session.flush()
        await qualities.seed(session, account.id)
        token = accounts.mint_session(session, account, SESSION_TTL)
        await session.commit()
        return account.id, token


async def _import(
    api: _Api, fixture: Fixture, built_at: datetime, account_id: uuid.UUID, drain: Drain
) -> None:
    """Upload the export, let the matcher work it, and bind by hand whatever it would not.

    The matcher is given every chance to do its job, and the fixture's own ids settle the
    rest through the review flow - which is exactly what an owner does with a row the
    matcher was unsure of, so the outcome is one the product produced rather than one
    written around it.
    """
    await api.post(
        "/api/import",
        expect=202,
        params={"name": "letterboxd-export.zip"},
        content=export(fixture, built_at),
    )
    await drain(account_id)
    by_name = {(film.title, film.year): film.tmdb_id for film in fixture.films}
    bound = 0
    while True:
        open_rows = [
            *(await api.get("/api/import/review"))["rows"],
            *(await api.get("/api/import/unmatched"))["rows"],
        ]
        if not open_rows:
            break
        row = open_rows[0]
        tmdb_id = by_name.get((row["name"], row["year"]))
        if tmdb_id is None:
            raise BuildFailed(
                f"the export names {row['name']} ({row['year']}), which the fixture does not"
            )
        await api.post(f"/api/import/rows/{row['id']}/film", {"tmdb_id": tmdb_id})
        bound += 1
    state = await api.get("/api/import")
    if state["status"] != "complete" or state["pending"]:
        raise BuildFailed(f"the import did not complete: {state}")
    log.info("demo build: import complete, %s rows bound by hand", bound)
    await drain(account_id)


async def _arrange(api: _Api, fixture: Fixture) -> None:
    """Move every film to its authored rank, best band first, top rank first.

    Settled top-down, each move lands the film under the ones already settled above it
    and above whatever has yet to be placed, so a rank is never off the end. A film
    already where the fixture puts it is left alone, which is what keeps the default
    order's own placements unmoved and the hand-ordered bands legibly hand-ordered.
    """
    moves = 0
    for band in fixture.wall:
        for rank, film in enumerate(band.films, start=1):
            standing = _standing(await api.get("/api/rated"))
            if film.tmdb_id not in standing:
                raise BuildFailed(f"{film.title} was not rated by the import")
            if standing[film.tmdb_id] == (band.band, rank):
                continue
            await api.post(f"/api/rated/{film.tmdb_id}/move", {"band": band.band, "rank": rank})
            moves += 1
    log.info("demo build: %s moves made", moves)


def _standing(rated: dict[str, Any]) -> dict[int, tuple[float, int]]:
    return {
        film["tmdb_id"]: (row["band"], film["rank"])
        for row in rated["rows"] or []
        for film in row["films"]
    }


async def _choose_qualities(api: _Api, fixture: Fixture) -> None:
    listed = {
        quality["name"]: quality["id"]
        for quality in (await api.get("/api/profile/qualities"))["qualities"]
    }
    missing = [name for name in fixture.qualities if name not in listed]
    if missing:
        raise BuildFailed(f"the quality list has no {missing}")
    await api.put(
        "/api/profile/qualities", {"quality_ids": [listed[name] for name in fixture.qualities]}
    )


async def _answer_criteria(api: _Api, fixture: Fixture) -> None:
    """A session's worth of criteria answers, judged the way the wall already judges.

    The card names its own opponent and quality; the fixture only says how many to
    answer. The verdict follows the wall - the film in the higher band did it better, and
    two films in one band tie - so the judgment history agrees with the ordering it sits
    beside rather than contradicting it at random.
    """
    band_of = fixture.band_of
    subject = fixture.criteria_session.tmdb_id
    card = (await api.post(f"/api/criteria/session/{subject}"))["card"]
    for answered in range(fixture.criteria_session.answers):
        if card is None:
            raise BuildFailed(f"the criteria session ran out after {answered} answers")
        a, b = band_of[card["film_a"]["tmdb_id"]], band_of[card["film_b"]["tmdb_id"]]
        verdict = "a" if a > b else "b" if b > a else "tied"
        card = (await api.post(f"/api/criteria/{card['id']}", {"verdict": verdict}))["card"]


async def _turn_down_suggestions(api: _Api, fixture: Fixture) -> None:
    """Dismiss the shelf's weakest suggestions, so the dismissed list has something on it."""
    shelf = (await api.get("/api/discovery", boundary="false"))["films"]
    for film in (
        shelf[len(shelf) - fixture.dismiss_suggestions :] if fixture.dismiss_suggestions else []
    ):
        await api.post(f"/api/discovery/{film['tmdb_id']}/dismissal")


async def _check(api: _Api, fixture: Fixture) -> None:
    """What a visitor would find, before the swap: the wall as authored, every surface stocked."""
    standing = _standing(await api.get("/api/rated"))
    wrong = [
        f"{film.title} at {standing.get(film.tmdb_id)}, wanted ({band.band}, {rank})"
        for band in fixture.wall
        for rank, film in enumerate(band.films, start=1)
        if standing.get(film.tmdb_id) != (band.band, rank)
    ]
    if wrong:
        raise BuildFailed("the wall is not as authored: " + "; ".join(wrong))
    if (await api.get("/api/profile"))["prose"] is None:
        raise BuildFailed("no prose profile was written; is the LLM provider configured?")
    if not (await api.get("/api/discovery", boundary="false"))["films"]:
        raise BuildFailed("the discovery shelf is empty")
    if not (await api.get("/api/watchlist/tier", boundary="false"))["unlocked"]:
        raise BuildFailed("the ranked tier did not unlock")


async def _swap_in(db: Database, account_id: uuid.UUID) -> None:
    """Retire the live demo and flag the new one, in one transaction.

    The flag goes on here and nowhere earlier. The spend is re-scoped to the shared
    ledger at the same moment: it was bought for everyone, and it has to outlive the
    account row a later rebuild will delete. The build's own session goes too - a
    visitor gets a session of their own from the door, and nothing else should hold one.
    """
    async with db.sessions() as session:
        await session.execute(delete(Account).where(Account.is_demo.is_(True)))
        await session.execute(
            update(SpendLedgerEntry)
            .where(SpendLedgerEntry.account_id == account_id)
            .values(account_id=None)
        )
        await session.execute(delete(AuthSession).where(AuthSession.account_id == account_id))
        await session.execute(
            update(Account).where(Account.id == account_id).values(email=DEMO_EMAIL, is_demo=True)
        )
        await session.commit()


# --- The process ---


async def _wait_for_worker(
    jobs_app: procrastinate.App, account_id: uuid.UUID, *, timeout: float, poll: float = 2.0
) -> None:
    """Block until the worker has run every job queued under this account's lock."""
    deadline = time.monotonic() + timeout
    while True:
        waiting = [
            job
            for status in ("todo", "doing")
            for job in await jobs_app.job_manager.list_jobs_async(
                status=status, lock=str(account_id)
            )
        ]
        if not waiting:
            return
        if time.monotonic() >= deadline:
            names = ", ".join(f"{job.task_name} ({job.status})" for job in waiting)
            raise BuildFailed(f"the worker did not finish the demo's jobs in time: {names}")
        await asyncio.sleep(poll)


async def run(settings: Settings) -> None:
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 0))
        async with httpx.AsyncClient(transport=transport, base_url="http://demo-build") as client:

            async def drain(account_id: uuid.UUID) -> None:
                await _wait_for_worker(
                    app.state.jobs, account_id, timeout=settings.demo_build_timeout_seconds
                )

            await build(client, app.state.db, Fixture.load(), drain=drain)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    # A replay is a few hundred requests, and one line per stage says more than one per call.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = Settings()
    sentry.install(settings)
    try:
        asyncio.run(run(settings))
    except BuildFailed:
        log.exception("the demo was not rebuilt; the previous demo account is left standing")
        sys.exit(1)


if __name__ == "__main__":
    main()
