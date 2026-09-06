"""The Discovery screen: the shelf of films the owner has never tracked.

A flat list of about twenty, ordered by the reranker with the linear scorer breaking
ties, each carrying the pitch that was written for it. Position is the entire public
statement (ADR 0005): there are no fit badges, no scores, no ranks, and no "97% match" -
the bucket that decided the order stays on the server, and only the sentence is shown.

*It lights at forming, and never before.* Discovery unlocks a whole readiness state
earlier than the ranked tier does, because anchor designations are the densest taste
signal an account emits and a fresh account needs a backlog filler
(onboarding-and-import.md). Below that the screen explains itself and shows the same
ambient progress the pre-gate Watchlist does - it does not fabricate a shelf from signal
that is not there.

*Nothing on this path can spend money or wait on anything.* Arriving may queue a restock,
which is the visit-gating discovery.md asks for - an owner who never opens the feed
causes no calls at all - but the response is whatever the last restock left behind. A
short shelf is served short: there is no padding and no degraded-mode banner, because a
feed that only shows what it can stand behind has nothing to apologise for.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from anchor import feed as feed_module
from anchor import jobs
from anchor import readiness as readiness_module
from anchor.accounts import CurrentAccount
from anchor.deps import AppJobs, AppSettings, DbSession
from anchor.models import Film
from anchor.profile import Progress
from anchor.readiness import Readiness

router = APIRouter(prefix="/api/discovery")


class Suggestion(BaseModel):
    """One card. Everything the screen draws, and nothing the engine thinks.

    The pitch is precomputed and comes out of the verdict; the plot rides along for the
    spoiler toggle the design puts on every surface that shows one. There is deliberately
    no fit, no bucket, no score and no rank field - not hidden, absent - so no client can
    render one by accident and no future screen can reach for one.
    """

    tmdb_id: int
    title: str
    year: int | None
    poster_path: str | None
    genres: list[str]
    directors: list[str]
    overview: str
    """The TMDB plot summary, shown behind the standard spoiler toggle."""
    pitch: str
    """The exemplar-grounded explanation, visible by default: "because you loved X and Y"."""

    @classmethod
    def of(cls, shelved: feed_module.Shelved) -> "Suggestion":
        film = shelved.film
        return cls(
            tmdb_id=film.tmdb_id,
            title=film.title,
            year=film.release_year,
            poster_path=film.poster_path,
            genres=list(film.genres),
            directors=_directors(film),
            overview=film.overview,
            pitch=shelved.verdict.explanation,
        )


class Feed(BaseModel):
    """The screen: the shelf, or the honest explanation of why there is not one yet."""

    readiness: Readiness
    unlocked: bool
    """The feed is live. Below *forming* the shelf is empty and ``progress`` says why."""
    progress: Progress | None
    films: list[Suggestion]
    """Up to about twenty, and fewer whenever the pipeline is thin. Never padded."""


@router.get("")
async def feed(
    account: CurrentAccount,
    db: DbSession,
    settings: AppSettings,
    jobs_app: AppJobs,
    boundary: bool = True,
) -> Feed:
    """The shelf as it now stands - which is also the moment a restock is queued.

    Arriving is the session boundary the shelf changes at, and the only read that may
    queue work: the screen reloading after the owner's own action says ``boundary=false``
    and gets back what is already there, so the list cannot move under their cursor.

    The restock is queued, never awaited. No interactive request in Anchor waits on a
    provider, and this one could not even if it wanted to - the module that dispatches is
    not loaded in this process (architecture.md).

    The one-time dot is not this endpoint's business: it is armed and cleared through
    ``unlocks``, which owns both of them, and the screen states its arrival there.
    """
    if boundary and await feed_module.due(db, account.id, settings):
        await jobs.schedule_restock(db, jobs_app, account.id)
        await db.commit()

    counted = await readiness_module.evidence(db, account.id)
    state = readiness_module.classify(counted, settings)
    if state is Readiness.cold:
        return Feed(
            readiness=state,
            unlocked=False,
            progress=Progress.toward(readiness_module.bars(counted, settings)[Readiness.forming]),
            films=[],
        )
    return Feed(
        readiness=state,
        unlocked=True,
        progress=None,
        films=[Suggestion.of(shelved) for shelved in await feed_module.shelf(db, account.id)],
    )


def _directors(film: Film) -> list[str]:
    return [str(person["name"]) for person in film.credits.get("directors") or []]
