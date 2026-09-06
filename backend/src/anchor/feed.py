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

*Worker-only, like every module that can spend money.* The LLM seam is imported inside
the function that dispatches, so importing this module never loads it - the web process
reads the shelf through :func:`shelf` and cannot rerank anything.
"""

import logging
import math
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import numpy as np
from sqlalchemy import delete, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from anchor import catalog, features, picker, prose, readiness, trainer
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
    """One card's worth: the film, and the judgment the card is standing on."""

    film: Film
    verdict: Verdict


async def shelf(db: AsyncSession, account_id: uuid.UUID) -> list[Shelved]:
    """The persisted shelf, in position order, read back verbatim.

    Nothing is decided here. The shelf is the statement the owner acted on, so it is
    stored rather than recomputed: a list rebuilt on every request would move under their
    cursor, and engine-driven changes land at session boundaries only (discovery.md).

    The one thing the read does enforce is the invariant, because the invariant is about
    what is *shown*. A restock only runs when the profile version moves, and in between
    the owner can add a shelved film from its own page or dismiss it - so a suggestion
    whose film has since become tracked or dismissed is left out here rather than waiting
    for the next rebuild to notice. The shelf simply runs one shorter, which is what it
    does whenever the pipeline has less to offer.
    """
    rows = await db.execute(
        select(Film, Verdict)
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
    return [Shelved(film=film, verdict=verdict) for film, verdict in rows]


async def due(db: AsyncSession, account_id: uuid.UUID, settings: Settings) -> bool:
    """Whether a restock would have anything to do.

    Gated on the profile version rather than on the shelf's length, because the version is
    what the verdict cache is keyed at: a restock at a version already restocked would
    re-source a few hundred candidates only to find every one of them already judged. An
    account whose pipeline is thin therefore sits with a short shelf until its taste moves
    - which is the honest outcome, and the cheap one.

    A run the provider cut short never stamped itself, so it stays due and the next visit
    picks up where it stopped, judging only what is still unjudged. That is what makes the
    capped state temporary rather than permanent: the shelf is short this month and fills
    itself in the next, without anybody being told anything went wrong.
    """
    if await readiness.state(db, account_id, settings) is Readiness.cold:
        return False
    live = await prose.latest(db, account_id)
    if live is None:
        return False  # nothing to rank a film against; activation never fabricates
    state = await _stored(db, account_id)
    return state is None or state.restocked_profile_version != live.version


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
        await _fill(session, account_id, version, films, settings)
        # Stamped only by a run that got all the way through. A restock that either
        # outside service cut short leaves the version unstamped, so it stays due and the
        # next arrival resumes it - which is what keeps every degraded state temporary.
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
    """Every film this account may not be suggested: tracked in any state, or dismissed.

    One set rather than two checks, because the invariant is one sentence - only
    untracked, undismissed films are ever suggested - and splitting it across the pipeline
    is how half of it eventually gets forgotten.
    """
    tracked = await db.scalars(
        select(AccountFilm.film_id).where(AccountFilm.account_id == account_id)
    )
    dismissed = await db.scalars(
        select(Dismissal.film_id).where(
            Dismissal.account_id == account_id, Dismissal.lifted_at.is_(None)
        )
    )
    return set(tracked) | set(dismissed)


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
    for window in _windows(todo, settings.discovery_rerank_window):
        try:
            ranked = await seam.rerank_candidates(account_id, profile, _candidates(window))
        except llm_module.Skipped as skipped:
            log.info("discovery rerank for %s stopped: %s", account_id, skipped)
            return False
        async with db.sessions() as session:
            for rank, answer in enumerate(ranked):
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


def _windows(films: Sequence[Film], size: int) -> list[Sequence[Film]]:
    return [films[start : start + size] for start in range(0, len(films), size)]


def _candidates(films: Sequence[Film]) -> list["Candidate"]:
    from anchor.llm import Candidate

    return [
        Candidate(
            tmdb_id=film.tmdb_id,
            title=film.title,
            year=film.release_year,
            genres=list(film.genres),
            directors=[str(person["name"]) for person in film.credits.get("directors") or []],
            overview=film.overview,
        )
        for film in films
    ]


# --- Filling the shelf ---


async def _fill(
    db: AsyncSession, account_id: uuid.UUID, version: int, films: Sequence[Film], settings: Settings
) -> None:
    """Rewrite the shelf from the verdicts that now exist.

    The never-pad rule is the whole of the ordering logic here. A film with no verdict at
    any version does not appear; a poor fit does not appear; and what is left sorts into
    two groups - the ones judged against the live profile, ranked as the reranker ranked
    them, and the stale ones behind them ordered by the linear scorer, which is the only
    honest thing to say about a judgment made of an older description of the owner. If
    that comes to nine films, the shelf holds nine.
    """
    fit = await _fit(db, account_id)
    verdicts = await _cached(db, account_id, [film.tmdb_id for film in films])
    shelved = []
    for film in films:
        verdict = verdicts.get(film.tmdb_id)
        if verdict is None or verdict.fit is FitBucket.poor_fit:
            continue
        current = verdict.profile_version == version
        score = fit.of_film(film) if fit is not None else 0.0
        # Live verdicts first and in the reranker's own order; stale ones behind them
        # ordered by the scorer, which is all a judgment of an older profile supports.
        key = (0, SHELF_ORDER[verdict.fit], verdict.rank, -score) if current else (1, 0, 0, -score)
        shelved.append((key, film, verdict))
    shelved.sort(key=lambda row: row[0])

    await db.execute(delete(Suggestion).where(Suggestion.account_id == account_id))
    await db.flush()
    for position, (_, film, verdict) in enumerate(shelved[: settings.discovery_shelf]):
        db.add(
            Suggestion(
                account_id=account_id,
                film_id=film.tmdb_id,
                verdict_id=verdict.id,
                position=position,
            )
        )


async def _cached(
    db: AsyncSession, account_id: uuid.UUID, film_ids: Sequence[int]
) -> dict[int, Verdict]:
    """The best verdict Anchor holds per film: the newest version it was judged at.

    Newest rather than live, because a verdict is append-only across versions and the
    older ones are what the degraded path serves. A film judged at the live version has
    its live verdict here by construction, since versions only go up.
    """
    if not film_ids:
        return {}
    rows = await db.scalars(
        select(Verdict)
        .where(Verdict.account_id == account_id, Verdict.film_id.in_(film_ids))
        .order_by(Verdict.film_id, Verdict.profile_version)
    )
    return {verdict.film_id: verdict for verdict in rows}


async def _stamp(db: AsyncSession, account_id: uuid.UUID, version: int) -> None:
    state = await _feed_state(db, account_id)
    state.restocked_profile_version = version
    state.restocked_at = datetime.now(UTC)


# --- The feed's own row ---


async def _stored(db: AsyncSession, account_id: uuid.UUID) -> FeedState | None:
    """This account's feed bookkeeping, or None where it has never had any."""
    state: FeedState | None = await db.scalar(
        select(FeedState).where(FeedState.account_id == account_id)
    )
    return state


async def _feed_state(db: AsyncSession, account_id: uuid.UUID) -> FeedState:
    """The same row, created if this is the first thing that ever needed one.

    Created by the two writers only - arming the dot and stamping a restock - so an
    account that never reaches *forming* accumulates no discovery rows at all. Reading
    the feed is not a reason to write to the database, and every read below treats a
    missing row as the answer it obviously is.
    """
    state = await _stored(db, account_id)
    if state is None:
        state = FeedState(account_id=account_id)
        db.add(state)
        await db.flush()
    return state
