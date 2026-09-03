"""Matching an export row to a TMDB film, and refusing to guess when it cannot.

Letterboxd rows carry a name, a year and a short link, but no TMDB id, so every row is
a search. The rules here are the whole of the judgment, and they are deliberately mean:

*Auto-accept only the unambiguous.* Either the normalized title plus the year (retried
at plus and minus one, because festival and wide-release years disagree) leaves exactly
one candidate, or one exact-title hit dominates the runner-up on popularity so heavily
that no person would pick the other. Everything else queues to review.

*One search per row, not three.* The year retries filter a single search response rather
than issuing a request each, which is the difference between six hundred TMDB calls for
a real export and eighteen hundred.

*Nothing matched is a state, not a failure.* TV-side entries and deleted films are
structurally unmatchable to a TMDB movie - ``/search/movie`` simply never returns them -
so the pipeline has an unmatched state rather than forcing every row to resolve
(onboarding-and-import.md).
"""

from dataclasses import dataclass

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from anchor import catalog
from anchor.errors import ApiError
from anchor.letterboxd import normalized
from anchor.settings import Settings
from anchor.tmdb import SearchHit, Tmdb

YEAR_SLACK = 1
"""Letterboxd may date a film by its festival premiere where TMDB dates the release."""


class Candidate(BaseModel):
    """One film the review screen offers for a row, with what tells two of them apart."""

    tmdb_id: int
    title: str
    year: int | None
    poster_path: str | None
    directors: list[str]


@dataclass(frozen=True)
class Match:
    """What the matcher made of one row.

    Exactly one of the three readings: ``accepted`` is a film sure enough to bind
    without asking, ``candidates`` is a question for the owner, and both empty is a row
    that found nothing.
    """

    accepted: int | None = None
    candidates: tuple[int, ...] = ()

    @property
    def unmatched(self) -> bool:
        return self.accepted is None and not self.candidates


async def match(tmdb: Tmdb, settings: Settings, name: str, year: int | None) -> Match:
    """Find the film one exported line means, or say honestly that it is not sure."""
    hits = await tmdb.search(name)
    if not hits:
        return Match()

    key = normalized(name)
    exact = [hit for hit in hits if normalized(hit.title) == key]

    if year is not None:
        for slack in range(YEAR_SLACK + 1):
            near = [hit for hit in exact if hit.year is not None and abs(hit.year - year) <= slack]
            if len(near) == 1:
                return Match(accepted=near[0].tmdb_id)

    dominant = _dominant(exact, settings.import_popularity_dominance)
    if dominant is not None:
        return Match(accepted=dominant)

    ranked = sorted(hits, key=lambda hit: -hit.popularity)
    offered = ranked[: settings.import_review_candidates]
    return Match(candidates=tuple(hit.tmdb_id for hit in offered))


def _dominant(exact: list[SearchHit], factor: float) -> int | None:
    """The one exact-title hit nothing else comes close to, or None if two might be it.

    A single exact-title hit dominates trivially, which is what carries a row whose year
    is missing: the title is unique on TMDB, so there is nothing for a person to choose
    between. Two similar films of the same name is exactly the case this must not decide.
    """
    if not exact:
        return None
    ranked = sorted(exact, key=lambda hit: -hit.popularity)
    if len(ranked) == 1:
        return ranked[0].tmdb_id
    return ranked[0].tmdb_id if ranked[0].popularity > factor * ranked[1].popularity else None


async def candidate_cards(
    db: AsyncSession, tmdb: Tmdb, settings: Settings, film_ids: list[int]
) -> dict[int, Candidate]:
    """Fill the shared store for the review screen's candidates, and read them back.

    The director is what tells two same-named films apart, and search results do not
    carry one, so each candidate costs its bundled call - once ever, shared across every
    account, and only for rows a person is actually looking at. A candidate TMDB has
    since dropped is left out rather than shown as a row that cannot be bound.
    """
    cards: dict[int, Candidate] = {}
    for film_id in dict.fromkeys(film_ids):
        try:
            film = await catalog.ensure_film(db, tmdb, film_id, settings.film_refresh_days)
        except ApiError as error:
            if error.status_code != 404:
                raise  # TMDB being down is not this candidate being unshowable
            continue
        cards[film_id] = Candidate(
            tmdb_id=film.tmdb_id,
            title=film.title,
            year=film.release_year,
            poster_path=film.poster_path,
            directors=[str(person["name"]) for person in film.credits.get("directors", [])],
        )
    return cards
