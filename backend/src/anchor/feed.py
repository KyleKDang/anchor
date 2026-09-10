"""The discovery pipeline: how a film gets from the wider catalog onto the shelf.

Four stages, in the order discovery.md fixes them. TMDB slices steered by the fit's own
top features, plus similar and recommendations seeded from the exemplar set, union to a
few hundred candidates; the linear scorer prefilters that to a shortlist of about sixty;
the LLM reranks the shortlist in windows and writes one verdict per film; and the top
twenty verdicts fill the shelf.

*The prefilter is the whole economy.* Everything above it is free - a score is a dot
product - and everything below it is a sentence somebody paid for. So the union is scored
on the facts TMDB's *list* rows already carry rather than bundled film by film, and only
the shortlist is fetched in full. Two hundred candidates the prefilter throws away cost
nothing at all.

*A verdict is a cache, keyed by profile version.* Anything already judged against the
live version skips the LLM entirely, poor-fits included - they are cached negatives,
never shown and never re-sent - so the second restock at one version is free and the
expensive one is the first after a bump.

*Nothing is ever padded.* One rule keeps every degraded state coherent: a film with no
verdict never reaches the shelf. Under a spend cap the current-version verdicts rank
normally, stale-version ones stay usable ordered by the linear scorer, and unverdicted
films simply wait. The shelf runs short, and says nothing about it, because the feed
never shows anything it cannot stand behind.

*The shelf is a view of the verdict cache, materialised at session boundaries.* The
restock buys verdicts and stops there; the shelf itself is rebuilt from them by
:func:`visit`, on the owner's arrival, for the price of two queries. That split is what
makes "engine-driven shelf changes land at session boundaries only" (discovery.md) a fact
about the code rather than a promise: a restock finishing while the owner is reading
writes nothing they can see, because there is nowhere for it to write. It is also what
makes the instant backfill instant - every candidate the shelf could want is already
judged and sitting in the cache.

*The spend is worker-only.* The web process imports this module to read the shelf, to run
the boundary, and to ask whether a restock is worth queueing, so what has to be
worker-only is not the module but the dispatch: the LLM seam is imported inside the one
function that calls it, and importing this module never loads it (architecture.md). A
request path physically cannot rerank anything.
"""

import logging
import math
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from anchor import catalog, demo, features, picker, prose, readiness, trainer
from anchor.db import Database
from anchor.errors import ApiError
from anchor.features import FeatureSpace
from anchor.models import (
    AccountFilm,
    Dismissal,
    Exemplar,
    ExemplarRole,
    FeedState,
    Film,
    FitBucket,
    Suggestion,
    SuggestionCooldown,
    Verdict,
    WeightVector,
)
from anchor.readiness import Readiness
from anchor.settings import Settings
from anchor.tmdb import SearchHit, Steer, Tmdb, TmdbUnavailable

if TYPE_CHECKING:
    # For the annotation only. Importing the seam for real would put it in the web
    # process, which is the one thing architecture.md's precompute rule forbids.
    from anchor.llm import Candidate, Llm

log = logging.getLogger(__name__)

STEERABLE = ("genre:", "director:", "cast:")
"""Feature kinds a discover slice can be pointed at.

Keywords are absent deliberately: the feature space stores keyword *names* and TMDB's
discover endpoint wants keyword ids, so steering on one would cost a resolution call per
slice to buy a slice that similar and recommendations already cover.
"""

SHELF_ORDER = {FitBucket.strong_fit: 0, FitBucket.plausible: 1}
"""How the two showable buckets sort. A poor fit has no place here and never gets one."""


# --- Reading the shelf ---


@dataclass(frozen=True)
class Shelved:
    """One card's worth: the film, the judgment behind it, and whether it is new."""

    film: Film
    verdict: Verdict
    fresh: bool
    """Arrived since the owner's last visit (surfacing.md).

    Freshness, not fit, so ADR 0005 is untouched: the marker says when the card landed
    and nothing about how good a match it is. It lives on the card and stops there -
    positions are not reordered for it and nothing at nav level counts it.
    """


async def shelf(db: AsyncSession, account_id: uuid.UUID) -> list[Shelved]:
    """The persisted shelf, in position order, read back verbatim.

    Nothing is decided here. The shelf is the statement the owner acted on, so it is
    stored rather than recomputed: a list rebuilt on every request would move under their
    cursor, and engine-driven changes land at session boundaries only (discovery.md).

    The one thing the read does enforce is the invariant, because the invariant is about
    what is *shown*. Between two boundaries the owner can add a shelved film from its own
    page - so a suggestion whose film has since become tracked or dismissed is left out
    here rather than waiting for the next rebuild to notice. The shelf simply runs one
    shorter, which is what it does whenever the pipeline has less to offer.
    """
    state = await _stored(db, account_id)
    since = state.fresh_since if state is not None else None
    rows = await db.execute(
        select(Film, Verdict, Suggestion.created_at)
        .select_from(Suggestion)
        .join(Film, Film.tmdb_id == Suggestion.film_id)
        .join(Verdict, Verdict.id == Suggestion.verdict_id)
        .where(
            Suggestion.account_id == account_id,
            ~exists().where(
                AccountFilm.account_id == account_id,
                AccountFilm.film_id == Suggestion.film_id,
            ),
            ~exists().where(
                Dismissal.account_id == account_id,
                Dismissal.film_id == Suggestion.film_id,
                Dismissal.lifted_at.is_(None),
            ),
        )
        .order_by(Suggestion.position)
    )
    return [
        # No line to measure against means no marker: on a first visit every card is new
        # to the owner, and saying so about all twenty is noise rather than information.
        Shelved(film=film, verdict=verdict, fresh=since is not None and arrived > since)
        for film, verdict, arrived in rows
    ]


async def due(db: AsyncSession, account_id: uuid.UUID, settings: Settings) -> bool:
    """Whether a restock would have anything to do, and be earned by anybody.

    Two gates, and they answer different questions. The profile version is what the
    verdict cache is keyed at, so a restock at a version already restocked would re-source
    a few hundred candidates only to find every one of them already judged. The visit is
    the economy: an owner who has not opened the feed since the last restock gets no new
    one however far their taste has moved, and an owner who has never opened it at all
    gets none ever, so ignoring discovery costs exactly nothing (discovery.md).

    The visit gate is invisible on the arrival path, because arriving is a visit and the
    boundary stamps it before this is asked. Where it bites is the other caller - the
    profile-version bump - which fires from a retrain the owner never went near, and which
    is asked this question exactly as the arrival is rather than being exempt from it.

    A run the provider cut short never stamped itself, so it stays due and the next visit
    picks up where it stopped, judging only what is still unjudged. That is what keeps the
    capped state temporary: the shelf is short this month and fills itself the next,
    without anybody being told anything went wrong.
    """
    # The demo is the one account whose visits are somebody else's. Its shelf was built
    # once and the spend that would refill it is exactly what a visitor must not be able
    # to trigger, so the gate that answers "was this earned by anybody?" answers no
    # (demo-account.md). The restock job re-asks this on its own way in, which is what
    # makes the answer hold for a job queued before the flag was set.
    if await demo.flagged(db, account_id):
        return False
    if await readiness.state(db, account_id, settings) is Readiness.cold:
        return False
    live = await prose.latest(db, account_id)
    if live is None:
        return False  # nothing to rank a film against; activation never fabricates
    state = await _stored(db, account_id)
    if state is None or state.visited_at is None:
        return False  # nobody has ever looked at this feed
    if state.restocked_at is not None and state.visited_at <= state.restocked_at:
        return False  # nothing has happened on the owner's side since the last one
    return state.restocked_profile_version != live.version


# --- The pipeline ---


async def restock(
    db: Database, tmdb: Tmdb, seam: "Llm", account_id: uuid.UUID, settings: Settings
) -> None:
    """Bring the shelf up to date for the account's live profile version.

    Sessions are opened per stage rather than held across the whole run: the middle of
    this is minutes of TMDB and provider calls, and a transaction held open across them
    would pin a connection for the duration and roll back everything the first two stages
    achieved if the third failed. The stages are each safe to repeat, so a job that dies
    half way through resumes rather than restarts.
    """
    async with db.sessions() as session:
        if not await due(session, account_id, settings):
            return
        live = await prose.latest(session, account_id)
        assert live is not None  # `due` is false without one
        profile, version = live.text, live.version
        fit = await _fit(session, account_id)
        if fit is None:
            return  # no vector, or a vocabulary with nothing in it: nothing to score with
        seeds = await _seeds(session, account_id, settings)
        people = await _people(session, account_id)
        known = await _known(session, account_id)
        excluded = await picker.exclusions(session, account_id)

    genres = await tmdb.genre_ids()
    sourced = await _source(tmdb, fit, seeds=seeds, people=people, genres=genres, settings=settings)
    shortlist = _prefilter(
        sourced, fit, known=known, excluded=excluded, genres=genres, settings=settings
    )

    films, bundled_all = await _bundled(db, tmdb, shortlist, settings)
    async with db.sessions() as session:
        judged = await _judged(session, account_id, version, films)
    judged_all = await _rerank(db, seam, account_id, profile, version, films, judged, settings)

    async with db.sessions() as session:
        # Nothing is written to the shelf here, deliberately. This job buys verdicts; the
        # shelf is re-derived from them at the owner's next arrival (:func:`visit`), so a
        # restock landing mid-session cannot move a card the owner is looking at.
        #
        # Stamped only by a run that got all the way through. A restock that either
        # outside service cut short leaves the version unstamped, so it stays due and the
        # next visit resumes it - which is what keeps every degraded state temporary.
        if bundled_all and judged_all:
            await _stamp(session, account_id, version)
        await session.commit()


# --- Sourcing ---


async def _source(
    tmdb: Tmdb,
    fit: "Fit",
    *,
    seeds: Sequence[int],
    people: Mapping[str, int],
    genres: Mapping[str, int],
    settings: Settings,
) -> list[SearchHit]:
    """The union: discover slices steered by the fit, plus neighbours of the exemplars.

    Deduplicated by film and capped, so a wildly productive slice cannot crowd the others
    out of the pool. The order films arrive in does not matter - the prefilter scores
    every one of them - so the cap is simply where sourcing stops being worth more calls.
    """
    found: dict[int, SearchHit] = {}
    for steer in _steers(fit, people=people, genres=genres, settings=settings):
        _collect(found, await tmdb.discover(steer), settings.discovery_pool)
    for film_id in seeds:
        _collect(found, await tmdb.similar(film_id), settings.discovery_pool)
        _collect(found, await tmdb.recommendations(film_id), settings.discovery_pool)
    return list(found.values())


def _collect(found: dict[int, SearchHit], hits: Iterable[SearchHit], cap: int) -> None:
    for hit in hits:
        if len(found) >= cap:
            return
        found.setdefault(hit.tmdb_id, hit)


def _steers(
    fit: "Fit", *, people: Mapping[str, int], genres: Mapping[str, int], settings: Settings
) -> list[Steer]:
    """One slice per top-weighted feature the fit names, best first.

    Positive weights only, because a slice steered at what the owner reliably dislikes
    would be sourcing candidates for the prefilter to throw out. A feature that cannot be
    turned into an id - a director TMDB never credited in this account's library - is
    skipped rather than approximated.
    """
    steers = []
    for column in fit.top(STEERABLE):
        kind, _, name = column.partition(":")
        if kind == "genre" and (genre_id := genres.get(name)) is not None:
            steers.append(Steer(genre_id=genre_id, min_votes=settings.discovery_min_votes))
        elif kind in ("director", "cast") and (person_id := people.get(name)) is not None:
            steers.append(Steer(person_id=person_id, min_votes=settings.discovery_min_votes))
        if len(steers) >= settings.discovery_slices:
            break
    return steers


async def _seeds(db: AsyncSession, account_id: uuid.UUID, settings: Settings) -> list[int]:
    """The exemplars the neighbour calls are seeded from: the owner's best, then anchors.

    The ordering's worst end is deliberately never a seed. "More like this" seeded from a
    film the owner disliked would source exactly the candidates the prefilter exists to
    reject, at the price of a call.
    """
    rows = await db.execute(
        select(Exemplar.film_id, Exemplar.role, Exemplar.rank, Exemplar.band).where(
            Exemplar.account_id == account_id,
            Exemplar.role.in_((ExemplarRole.best, ExemplarRole.anchor)),
        )
    )
    ordered = sorted(
        rows,
        # Best-first within each role, and the best end before the anchors: an anchor is
        # the exemplar of a band, and a 2.0 anchor is a definition rather than a liking.
        key=lambda row: (0 if row.role is ExemplarRole.best else 1, row.rank),
    )
    seeds: list[int] = []
    for row in ordered:
        if row.film_id not in seeds:
            seeds.append(row.film_id)
        if len(seeds) >= settings.discovery_seeds:
            break
    return seeds


async def _people(db: AsyncSession, account_id: uuid.UUID) -> dict[str, int]:
    """TMDB person ids for the names the fit knows, read off the account's own library.

    The feature space is keyed on names because that is what a film's credits spell out,
    and discover wants ids - but every name in the space came from a stored film that
    also carries the id beside it, so the map is free and no lookup call is needed.
    """
    rows = await db.scalars(
        select(Film)
        .join(AccountFilm, AccountFilm.film_id == Film.tmdb_id)
        .where(AccountFilm.account_id == account_id)
    )
    found: dict[str, int] = {}
    for film in rows:
        for role in ("directors", "cast"):
            for person in film.credits.get(role) or []:
                name, person_id = person.get("name"), person.get("id")
                if isinstance(name, str) and isinstance(person_id, int):
                    found.setdefault(name, person_id)
    return found


# --- The prefilter ---


@dataclass(frozen=True)
class Fit:
    """The account's linear scorer, ready to score with."""

    space: FeatureSpace
    weights: np.ndarray

    def top(self, kinds: Sequence[str]) -> list[str]:
        """Columns of the given kinds, most positively weighted first."""
        weighted = [
            (weight, column)
            for column, weight in zip(self.space.columns, self.weights, strict=True)
            if weight > 0 and column.startswith(tuple(kinds))
        ]
        return [column for _, column in sorted(weighted, key=lambda pair: -pair[0])]

    def of_row(self, symbols: Iterable[str], priors: tuple[float, float]) -> float:
        return float(self.space.row(symbols, priors) @ self.weights)

    def of_film(self, film: Film) -> float:
        return trainer.score(self.weights, self.space, film)

    def popularity(self, vote_count: int) -> float:
        return self.space.standardised(features.POPULARITY_PRIOR, math.log1p(vote_count))


async def _fit(db: AsyncSession, account_id: uuid.UUID) -> Fit | None:
    vector: WeightVector | None = await db.scalar(
        select(WeightVector).where(WeightVector.account_id == account_id)
    )
    if vector is None:
        return None
    space = FeatureSpace.from_json(vector.space)
    if not space.columns:
        return None  # a library with nothing shared in it defines no space to score in
    weights = np.array([vector.weights.get(column, 0.0) for column in space.columns])
    return Fit(space=space, weights=weights)


def _prefilter(
    sourced: Sequence[SearchHit],
    fit: Fit,
    *,
    known: set[int],
    excluded: picker.Exclusions,
    genres: Mapping[str, int],
    settings: Settings,
) -> list[SearchHit]:
    """The union cut down to the shortlist the LLM will actually be shown.

    Two kinds of cut, and they are not the same kind of thing. The exclusions are
    mechanical and absolute - a tracked film, a dismissed one, a genre or language the
    owner has ruled out - and nothing scores its way past them. The rest is ranking, and
    the popularity damper is part of it: a candidate is worth its score less a slice of
    its own standardised popularity, so the deep cut and the blockbuster the fit likes
    equally do not arrive equally. Soft, with no hard cap, exactly as discovery.md asks.
    """
    named = {genre_id: name for name, genre_id in genres.items()}
    scored = []
    for hit in sourced:
        if hit.tmdb_id in known:
            continue
        listed = [named[genre_id] for genre_id in hit.genre_ids if genre_id in named]
        if excluded.excludes(listed, hit.original_language):
            continue
        score = fit.of_row(
            (f"genre:{name}" for name in listed),
            (hit.vote_average, math.log1p(hit.vote_count)),
        )
        damped = score - settings.discovery_popularity_damper * fit.popularity(hit.vote_count)
        scored.append((damped, hit))
    scored.sort(key=lambda pair: (-pair[0], pair[1].tmdb_id))
    return [hit for _, hit in scored[: settings.discovery_shortlist]]


async def _known(db: AsyncSession, account_id: uuid.UUID) -> set[int]:
    """Every film this account may not be suggested now: tracked, dismissed, or cooling off.

    One set rather than three checks, because the invariant is one sentence - only
    untracked, undismissed films are ever suggested - and splitting it across the pipeline
    is how part of it eventually gets forgotten.

    A film inside its re-entry cooldown is here for a different reason from the other two:
    it is not permanently ineligible, it is merely not wanted yet. Excluding it costs
    nothing and saves a bundled call, because its verdict is already cached - so when the
    cooldown expires the shelf picks it back up without this pipeline running at all.
    """
    tracked = await db.scalars(
        select(AccountFilm.film_id).where(AccountFilm.account_id == account_id)
    )
    dismissed = await db.scalars(
        select(Dismissal.film_id).where(
            Dismissal.account_id == account_id, Dismissal.lifted_at.is_(None)
        )
    )
    counter = await _refresh_counter(db, account_id)
    cooling = await db.scalars(
        select(SuggestionCooldown.film_id).where(
            SuggestionCooldown.account_id == account_id,
            SuggestionCooldown.reentry_refresh > counter,
        )
    )
    return set(tracked) | set(dismissed) | set(cooling)


async def _bundled(
    db: Database, tmdb: Tmdb, shortlist: Sequence[SearchHit], settings: Settings
) -> tuple[list[Film], bool]:
    """The shortlist as catalog rows, in prefilter order: one bundled call per new film.

    This is where the sixty become real films with directors and keywords - the reranker
    needs them, and so does the linear tie-break, which reads the full feature vector
    rather than the partial one the prefilter scored on. A film TMDB has dropped since it
    answered the slice is left out rather than failing the restock.

    Answers what it fetched and whether that was all of it, so a run TMDB cut short can be
    left unstamped and resumed, the same way one the provider cut short is.
    """
    films = []
    for hit in shortlist:
        async with db.sessions() as session:
            try:
                films.append(
                    await catalog.ensure_film(
                        session, tmdb, hit.tmdb_id, settings.film_refresh_days
                    )
                )
            except ApiError as error:
                if error.status_code == 404:
                    continue  # TMDB has dropped it; it is simply not a candidate
                raise
            except TmdbUnavailable:
                # Down or throttling past its retries: keep what was fetched and let the
                # shelf be built from it. The next restock resumes from the cache.
                log.warning("TMDB unavailable mid-restock; %s films bundled", len(films))
                return films, False
    return films, True


# --- The rerank ---


async def _judged(
    db: AsyncSession, account_id: uuid.UUID, version: int, films: Sequence[Film]
) -> set[int]:
    """Films already judged at the live version, poor fits included.

    Poor fits are the point of including them: they are cached negatives, so a film the
    reranker has already rejected is never sent back to it, however many restocks later
    the slice that found it runs again.
    """
    rows = await db.scalars(
        select(Verdict.film_id).where(
            Verdict.account_id == account_id,
            Verdict.profile_version == version,
            Verdict.film_id.in_([film.tmdb_id for film in films]),
        )
    )
    return set(rows)


async def _rerank(
    db: Database,
    seam: "Llm",
    account_id: uuid.UUID,
    profile: str,
    version: int,
    films: Sequence[Film],
    judged: set[int],
    settings: Settings,
) -> bool:
    """Judge the unjudged, a window at a time, writing each window's verdicts as it lands.

    Windowed because a listwise ranking is only as good as the model's attention over its
    list, and because one call over the whole shortlist would put a month's budget behind
    a single provider timeout. The windows are cut from the prefilter's order, so the
    strongest candidates are judged against each other rather than scattered.

    Every window commits on its own. A cap reached half way through leaves the windows
    that landed cached and the rest unjudged, and the shelf is built from what there is -
    which is exactly the degraded state discovery.md describes, arrived at by doing less
    rather than by a special case. Answering False is what keeps that state temporary: the
    restock does not stamp itself done, so the next visit resumes at the window it stopped
    on and the films that waited are judged then.
    """
    from anchor import llm as llm_module

    todo = [film for film in films if film.tmdb_id not in judged]
    for start, window in _windows(todo, settings.discovery_rerank_window):
        try:
            ranked = await seam.rerank_candidates(account_id, profile, _candidates(window))
        except llm_module.Skipped as skipped:
            llm_module.log_skip(log, skipped, "discovery rerank for %s stopped", account_id)
            return False
        async with db.sessions() as session:
            for rank, answer in enumerate(ranked, start=start):
                session.add(
                    Verdict(
                        account_id=account_id,
                        film_id=answer.tmdb_id,
                        profile_version=version,
                        fit=answer.fit,
                        explanation=answer.explanation.strip(),
                        rank=rank,
                    )
                )
            await session.commit()
    return True


def _windows(films: Sequence[Film], size: int) -> list[tuple[int, Sequence[Film]]]:
    """The shortlist cut into windows, each with the offset its ranks are counted from."""
    return [(start, films[start : start + size]) for start in range(0, len(films), size)]


def _candidates(films: Sequence[Film]) -> list["Candidate"]:
    from anchor.llm import Candidate

    return [
        Candidate(
            tmdb_id=film.tmdb_id,
            title=film.title,
            year=film.release_year,
            genres=list(film.genres),
            directors=catalog.names(film, "directors"),
            overview=film.overview,
        )
        for film in films
    ]


# --- The session boundary ---


async def visit(db: AsyncSession, account_id: uuid.UUID, settings: Settings) -> None:
    """The owner arriving at the feed: the one moment the shelf is allowed to change.

    Everything engine-driven happens here and nowhere else, which is how discovery.md's
    rule ends up being structural rather than a promise. A restock running in the worker
    while the owner reads writes verdicts and nothing else, so there is no path by which
    the list can move under their cursor - the next arrival is what expresses it.

    Three things, in the order they have to happen. The counter moves first, because it
    is the clock everything below is denominated in. Then the cards that have been passed
    over long enough rotate off, taking a re-entry cooldown with them. Then the shelf is
    re-derived from the verdict cache, which is where a restock's work finally shows up
    and where the rotated slots get refilled.

    Both of data-model.md's names are for this one moment: it is the *visit* the restock
    gate reads and the *refresh* rotation is counted in, which is why the row carries a
    timestamp and a counter for what is, from the owner's side, opening a screen.

    The visit line moves last, and only by one step: what was the last visit becomes the
    line freshness is measured against, and now becomes the last visit. Holding it one
    behind is what lets the marker survive the session it is shown in - every reload
    after an action marks the same cards, because the line does not move again until the
    owner comes back.

    It moves only where the owner had a shelf to look at last time, which is the one
    subtlety in the whole thing. An arrival that found nothing - the visit that queues an
    account's very first restock, or any visit while the pipeline is empty - is not a
    visit they could have seen a card at, so measuring the next one against it would mark
    a whole first shelf "new since your last visit" and say nothing at all.

    The demo account has no session boundary at all, because its arrivals are not its
    owner's: rotating a card off because visitors have passed it over would let the crowd
    edit the shelf the fixture built. Nothing here runs for it, so the counter, the
    cooldowns and the freshness line all stay where the build left them.
    """
    if await demo.flagged(db, account_id):
        return
    state = await _feed_state(db, account_id)
    state.refresh_counter += 1
    had_shelf = await _standing(db, account_id) > 0
    await _rotate(db, account_id, state.refresh_counter, settings)
    await rebuild(db, account_id, settings)
    if had_shelf:
        state.fresh_since = state.visited_at
    # Stamped on every arrival regardless, because this half is the spend gate rather
    # than the marker: an owner who arrives to an empty shelf has still arrived, and it is
    # exactly that arrival which earns them the restock that fills it.
    #
    # Read from the database rather than from this process, because it is compared against
    # ``Suggestion.created_at``, which the database stamps. Two clocks either side of a
    # ">" is how a marker ends up stuck on or stuck off from a second of skew - and in
    # Postgres this is transaction-start time, so a card written by this very transaction
    # carries exactly this value and is correctly not newer than it.
    state.visited_at = await db.scalar(select(func.now()))


async def _standing(db: AsyncSession, account_id: uuid.UUID) -> int:
    """How many cards the shelf is holding right now."""
    count = await db.scalar(
        select(func.count()).select_from(Suggestion).where(Suggestion.account_id == account_id)
    )
    return int(count or 0)


async def _rotate(
    db: AsyncSession, account_id: uuid.UUID, counter: int, settings: Settings
) -> None:
    """Retire the cards the owner has now passed over often enough, with a cooldown.

    Passed over, not rejected: a card the owner acted on left the shelf when they acted,
    so everything still here at its third refresh is something they have looked at and
    said nothing about. Rotation is never announced (surfacing.md) - the shelf is simply
    its new self.

    The verdict behind the film is deliberately untouched, so the film's return costs
    nothing at all: when the cooldown expires the rebuild picks it up from the same
    cached judgment, and nobody pays to think about it again.
    """
    stale = await db.scalars(
        select(Suggestion).where(
            Suggestion.account_id == account_id,
            Suggestion.arrived_at_refresh <= counter - settings.discovery_rotation_refreshes,
        )
    )
    for suggestion in stale:
        await _cool_down(
            db, account_id, suggestion.film_id, counter + settings.discovery_reentry_refreshes
        )
        await db.delete(suggestion)
    await db.flush()


async def _cool_down(db: AsyncSession, account_id: uuid.UUID, film_id: int, until: int) -> None:
    """Hold a film off the shelf until the counter reaches ``until``; extend an existing hold."""
    cooldown: SuggestionCooldown | None = await db.scalar(
        select(SuggestionCooldown).where(
            SuggestionCooldown.account_id == account_id, SuggestionCooldown.film_id == film_id
        )
    )
    if cooldown is None:
        db.add(SuggestionCooldown(account_id=account_id, film_id=film_id, reentry_refresh=until))
    else:
        cooldown.reentry_refresh = until


# --- Filling the shelf ---


@dataclass(frozen=True)
class _Contender:
    """One film the shelf could show, with the sort key that decides whether it does.

    Not a *candidate*: that word is already taken in this module by the thing the LLM is
    shown (:class:`anchor.llm.Candidate`), which is a film on its way *into* the verdict
    cache. This is one on its way out of it.
    """

    film_id: int
    verdict_id: uuid.UUID
    key: tuple[int, int, int, float, int]


async def rebuild(db: AsyncSession, account_id: uuid.UUID, settings: Settings) -> None:
    """Re-derive the whole shelf from the verdict cache, best first.

    The never-pad rule is the whole of the ordering logic. A film with no verdict at any
    version does not appear; a poor fit does not appear; and what is left sorts into two
    groups - the ones judged against the live profile, ranked as the reranker ranked them,
    and the stale ones behind them ordered by the linear scorer, which is the only honest
    thing to say about a judgment made of an older description of the owner. If that comes
    to nine films, the shelf holds nine.
    """
    ordered = await _ordered(db, account_id)
    await _materialise(db, account_id, ordered[: settings.discovery_shelf], repitch=True)


async def backfill(db: AsyncSession, account_id: uuid.UUID, settings: Settings) -> None:
    """Close the gap an owner action left, and top the shelf back up from the cache.

    Not a rebuild, deliberately. An action is the owner's own doing rather than a session
    boundary, so the cards they did not touch are left entirely alone: the gap closes, and
    one candidate joins the end. The newcomer is the next-ranked film the cache already
    holds a verdict for, which is why this costs two queries and no provider call at all
    (discovery.md).

    Entirely alone includes the sentence each card is standing on. A restock that landed
    while the owner was reading has written newer verdicts, and expressing them here would
    rewrite the pitch under their cursor - an engine-driven change outside a session
    boundary, which is the one thing this whole arrangement exists to make impossible. So
    the re-pitch is withheld: it is the next arrival's to make.
    """
    standing = {
        film_id: position
        for film_id, position in await db.execute(
            select(Suggestion.film_id, Suggestion.position).where(
                Suggestion.account_id == account_id
            )
        )
    }
    ordered = await _ordered(db, account_id)
    held = sorted(
        (one for one in ordered if one.film_id in standing), key=lambda one: standing[one.film_id]
    )
    joining = [one for one in ordered if one.film_id not in standing]
    await _materialise(db, account_id, (held + joining)[: settings.discovery_shelf], repitch=False)


async def _ordered(db: AsyncSession, account_id: uuid.UUID) -> list[_Contender]:
    """Every film this account could be shown right now, in the order the shelf wants them.

    The eligibility rules are the shelf's invariant restated as a query: a film the owner
    tracks in any state, one they have dismissed and not lifted, and one still inside its
    re-entry cooldown are all out, whatever the cache thinks of them.
    """
    live = await prose.latest(db, account_id)
    version = live.version if live is not None else None
    fit = await _fit(db, account_id)
    counter = await _refresh_counter(db, account_id)
    rows = await db.execute(
        select(Film, Verdict)
        .join(Verdict, Verdict.film_id == Film.tmdb_id)
        .where(
            Verdict.account_id == account_id,
            ~exists().where(
                AccountFilm.account_id == account_id, AccountFilm.film_id == Verdict.film_id
            ),
            ~exists().where(
                Dismissal.account_id == account_id,
                Dismissal.film_id == Verdict.film_id,
                Dismissal.lifted_at.is_(None),
            ),
            ~exists().where(
                SuggestionCooldown.account_id == account_id,
                SuggestionCooldown.film_id == Verdict.film_id,
                SuggestionCooldown.reentry_refresh > counter,
            ),
        )
        # Newest version last, so the loop below keeps the newest verdict per film: a
        # bump appends rather than replaces, and the older rows are what a degraded read
        # is served from.
        .order_by(Verdict.profile_version)
    )
    best: dict[int, tuple[Film, Verdict]] = {}
    for film, verdict in rows:
        best[film.tmdb_id] = (film, verdict)

    contenders = []
    for film, verdict in best.values():
        if verdict.fit is FitBucket.poor_fit:
            continue
        score = fit.of_film(film) if fit is not None else 0.0
        # Live verdicts first and in the reranker's own order; stale ones behind them
        # ordered by the scorer, which is all a judgment of an older profile supports.
        # The id last, so a tie between two films is broken the same way every time.
        contenders.append(
            _Contender(
                film_id=film.tmdb_id,
                verdict_id=verdict.id,
                key=(0, SHELF_ORDER[verdict.fit], verdict.rank, -score, film.tmdb_id)
                if verdict.profile_version == version
                else (1, 0, 0, -score, film.tmdb_id),
            )
        )
    contenders.sort(key=lambda one: one.key)
    return contenders


async def _materialise(
    db: AsyncSession,
    account_id: uuid.UUID,
    desired: Sequence[_Contender],
    *,
    repitch: bool,
) -> None:
    """Make the stored shelf say exactly this, keeping the rows of films that stay.

    Reconciled rather than rewritten, and that is not an optimisation. A card's row is
    where its two clocks live - when it landed, and which refresh it landed at - so
    deleting and re-inserting a film that never left the shelf would reset its rotation
    counter every time the engine ran and mark it new to an owner who has been looking at
    it for a week.

    ``repitch`` is the session boundary, spelled as an argument. A held card's verdict is
    the sentence the owner is reading, so moving it to a newer one is an engine-driven
    change and belongs only to an arrival; a backfill closing a gap mid-session passes
    False and leaves every surviving card exactly as it found it.
    """
    counter = await _refresh_counter(db, account_id)
    existing = {
        row.film_id: row
        for row in await db.scalars(select(Suggestion).where(Suggestion.account_id == account_id))
    }
    for position, contender in enumerate(desired):
        held = existing.pop(contender.film_id, None)
        if held is None:
            db.add(
                Suggestion(
                    account_id=account_id,
                    film_id=contender.film_id,
                    verdict_id=contender.verdict_id,
                    position=position,
                    arrived_at_refresh=counter,
                )
            )
        else:
            held.position = position
            if repitch:
                held.verdict_id = contender.verdict_id
    for leaving in existing.values():
        await db.delete(leaving)
    await db.flush()


async def _stamp(db: AsyncSession, account_id: uuid.UUID, version: int) -> None:
    state = await _feed_state(db, account_id)
    state.restocked_profile_version = version
    # Left to the database for the same reason ``visited_at`` is read from it, and against
    # the same comparison: the spend gate asks whether the visit came after the restock,
    # and two clocks either side of a "<=" is #67 again. This half is written by the
    # worker, whose process clock is a different machine's from Postgres's, so taking it
    # from here would let a worker that leads stamp the future - and every arrival until
    # the database caught up would decline to restock. A silent freeze rather than an
    # overspend, which is the worse kind. Sent as SQL rather than fetched first, so there
    # is no Python value for a process clock to get into.
    state.restocked_at = func.now()
    # Counted here rather than at the start, so the counter means completed restocks: it
    # is the denominator the accept and dismissal rates are read against (evaluation.md),
    # and a run the provider cut short bought the owner no cards to answer.
    state.restock_counter += 1


async def note_answer(db: AsyncSession, account_id: uuid.UUID, *, accepted: bool) -> None:
    """Count one card answered, for the operator's accept and dismissal rates.

    Counted rather than derived from what the answer wrote, because neither side is
    durable in the shape the rate needs. An accept writes an account-film that removing
    the film from the backlog deletes outright, so the worst accepts would quietly leave
    the numerator. A dismissal does leave a row, but that row runs from the account's
    first day while the restock counter runs from the day it was added, and a rate whose
    two sides cover different spans is not a rate. Turning a lifted film down again counts
    as the second answer it was.

    Measurement only, and nothing in the feed's own rules reads either counter (ADR 0012).
    """
    state = await _feed_state(db, account_id)
    if accepted:
        state.accept_counter += 1
    else:
        state.dismissal_counter += 1


# --- The feed's own row ---


async def _refresh_counter(db: AsyncSession, account_id: uuid.UUID) -> int:
    """The feed's clock, and zero for an account that has never had a row written."""
    state = await _stored(db, account_id)
    return state.refresh_counter if state is not None else 0


async def _stored(db: AsyncSession, account_id: uuid.UUID) -> FeedState | None:
    """This account's feed bookkeeping, or None where it has never had any."""
    state: FeedState | None = await db.scalar(
        select(FeedState).where(FeedState.account_id == account_id)
    )
    return state


async def _feed_state(db: AsyncSession, account_id: uuid.UUID) -> FeedState:
    """The same row, created if this is the first thing that ever needed one.

    Created by the two writers only - the owner arriving, and a restock stamping itself -
    so an account that never reaches *forming* accumulates no discovery rows at all.
    Reading the feed is not a reason to write to the database, and every read above treats
    a missing row as the answer it obviously is.
    """
    state = await _stored(db, account_id)
    if state is None:
        state = FeedState(account_id=account_id)
        db.add(state)
        await db.flush()
    return state
