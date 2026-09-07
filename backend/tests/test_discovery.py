"""The discovery shelf: what reaches it, in what order, and what it cost to put there.

These read as what the owner did - rate a handful of films, mark an anchor, open
Discovery - and assert what appeared on the shelf and what was bought to fill it. TMDB is
faked at its HTTP edge and the provider is scripted at the seam, so which films exist and
what the reranker thinks of them are the test's own decisions; what is under test is
everything between.

The claims that matter most here are negative ones, and the suite is weighted that way.
A feed that pads to twenty, suggests a film the owner has already seen, re-asks about a
film it has already rejected, or shows a badge saying how good the match is would pass a
happy-path suite perfectly well, and would be a different product.

Nothing here asserts which candidate the prefilter happened to rank third. That is the
advisory math's business (ADR 0001, testing.md), and a scripted answer that named films
by position would be pinning the damper's arithmetic rather than the feed's behaviour -
so answers are scripted over the whole candidate set, and the seam's own rule that a film
which was not offered cannot be ranked does the rest.
"""

import uuid

import pytest

from anchor import llm
from faketmdb import FilmFixture
from flows import (
    account_id,
    build_ordering,
    discovery,
    mark_anchor,
    seen_discovery,
    shelf,
    thumb_down,
    unlocks,
)
from invariants import (
    assert_shelf_stands_on_verdicts,
    dismiss,
    prose_versions,
    spend_ledger,
    verdicts,
)

PIPELINE = dict(
    readiness_forming_films=3,
    readiness_forming_bands=1,
    prose_placements_trigger=2,
    discovery_shelf=3,
    discovery_shortlist=6,
    discovery_min_votes=0,
)
"""Small bars and a small pipeline, so a test spends five placements and a call or two
rather than fifty and twelve saying something that is true at any size. The dimensions
and the stages are spec; every number here is tuning."""

pytestmark = pytest.mark.settings(**PIPELINE, discovery_rerank_window=10)
"""One window by default, so a scripted ranking is the whole ranking and the shelf's order
is the test's own statement. The tests that are *about* windowing narrow it themselves."""

WINDOWED = pytest.mark.settings(**PIPELINE, discovery_rerank_window=2)

RATED = (
    FilmFixture(2000, "Once Upon a Time in the West", genres=("Western",), directors=("Leone",)),
    FilmFixture(2001, "The Good, the Bad and the Ugly", genres=("Western",), directors=("Leone",)),
    FilmFixture(2002, "A Fistful of Dollars", genres=("Western",), directors=("Leone",)),
    FilmFixture(2003, "Saw", genres=("Horror",), directors=("Wan",)),
    FilmFixture(2004, "Saw II", genres=("Horror",), directors=("Bousman",)),
)
"""What the owner has rated: westerns at the top, horror at the bottom.

A shape rather than a list, because the fit is what steers the sourcing - a library the
scorer can find no pattern in produces slices pointed nowhere, and a test built on one
would be asserting against noise.
"""

CANDIDATES = (
    FilmFixture(3000, "Django", genres=("Western",), directors=("Corbucci",), vote_count=900),
    FilmFixture(3001, "The Big Silence", genres=("Western",), directors=("Corbucci",)),
    FilmFixture(3002, "Day of Anger", genres=("Western",), directors=("Valerii",), vote_count=200),
    FilmFixture(3003, "Ringo", genres=("Western",), directors=("Tessari",), vote_count=100),
)
"""Untracked films TMDB offers back. Never rated, never added, never dismissed."""

SUBTITLED = FilmFixture(
    3100, "Il Grande Silenzio", genres=("Western",), directors=("Corbucci",), original_language="it"
)
SCARY = FilmFixture(3101, "Suspiria", genres=("Horror",), directors=("Argento",))
MORE = (
    FilmFixture(2005, "Rio Bravo", genres=("Western",), directors=("Hawks",)),
    FilmFixture(2006, "The Searchers", genres=("Western",), directors=("Ford",)),
)
"""Films to rate later, for the tests that need the taste profile to move on."""


@pytest.fixture(autouse=True)
def stocked(tmdb):
    """TMDB's catalog, and what it says is near the owner's favourite film."""
    return tmdb.with_films(*RATED, *CANDIDATES).with_neighbours(RATED[0].tmdb_id, *CANDIDATES)


def ranked(*films, fit="strong_fit"):
    """One scripted rerank answer: these films, in this order, all at this bucket.

    Handed the whole candidate set on purpose. The seam drops anything that was not
    offered, so the same answer is a correct answer to every window, and no test has to
    know which films the prefilter put in which one.
    """
    return {"ranked": [_judged(film, fit) for film in films]}


def mixed(*pairs):
    """A rerank answer with a bucket per film, for the tests about how buckets sort."""
    return {"ranked": [_judged(film, fit) for film, fit in pairs]}


def _judged(film, fit):
    return {
        "tmdb_id": film.tmdb_id,
        "fit": fit,
        "explanation": f"Because you loved {film.title}.",
    }


def ids(films):
    return {film["tmdb_id"] for film in films}


async def rating_films(client, run_jobs):
    """An account at *forming* with its first prose written: the state discovery needs.

    Westerns high and horror low, with an anchor at each end, which is what gives the fit
    a shape and the exemplar set something to seed the neighbour calls with.
    """
    await build_ordering(client, RATED[:3], band=4.0)
    await build_ordering(client, RATED[3:], band=2.0)
    await mark_anchor(client, RATED[0])
    await mark_anchor(client, RATED[4])
    await run_jobs()
    return uuid.UUID(await account_id(client))


async def visit(client, run_jobs):
    """Open Discovery, let any restock it queued run, and open it again.

    Two reads because nothing on the request path may wait on a provider: arriving is what
    queues the work, and what the owner sees is whatever the last restock left behind. It
    is the honest shape of the feature, so the tests are written in it rather than around
    it - and it is why an account's very first shelf is usually already there, since the
    prose profile that lit the feed up scheduled the restock on its way out.
    """
    await discovery(client)
    await run_jobs()
    return await shelf(client)


# --- Lighting up ---


async def test_a_cold_account_gets_an_explanation_rather_than_a_shelf(owner):
    """Activation never fabricates from zero signal: it says what it is waiting for."""
    feed = await discovery(owner)

    assert feed["readiness"] == "cold"
    assert feed["unlocked"] is False
    assert feed["films"] == []
    assert feed["progress"]["thresholds"], "the pre-gate screen has nothing to explain itself with"


async def test_a_cold_account_never_reaches_a_provider(owner, run_jobs, db, provider):
    """The pre-gate screen is a screen, not a pipeline: opening it buys nothing."""
    await discovery(owner)
    await run_jobs()

    assert provider.dispatched == 0
    assert await spend_ledger(db) == []


async def test_the_dot_arms_at_forming_and_clears_on_the_first_visit(owner, run_jobs):
    """The one-time dot: the only nav-level marker the feed ever gets (surfacing.md).

    Armed by readiness crossing *forming* and cleared by the arrival the screen states,
    both of which belong to ``unlocks`` rather than to the feed. What this ticket adds is
    that there is now something to arrive at.
    """
    assert (await unlocks(owner))["discovery"] is False

    await rating_films(owner, run_jobs)
    assert (await unlocks(owner))["discovery"] is True

    await discovery(owner)
    await seen_discovery(owner)
    assert (await unlocks(owner))["discovery"] is False


# --- Sourcing and the prefilter ---


async def test_the_shelf_fills_from_films_the_owner_has_never_tracked(owner, run_jobs, provider):
    """The whole point, end to end: rate a few films, and the feed has films to offer."""
    provider.will_say(**ranked(CANDIDATES[0], CANDIDATES[1]))
    await rating_films(owner, run_jobs)

    films = await visit(owner, run_jobs)

    assert [film["tmdb_id"] for film in films] == [3000, 3001]
    assert films[0]["pitch"] == "Because you loved Django."


async def test_sourcing_steers_discover_at_the_fit_and_seeds_from_the_exemplars(
    owner, run_jobs, tmdb
):
    """Slices pointed somewhere, plus TMDB's own neighbours of the owner's best films."""
    await rating_films(owner, run_jobs)
    await visit(owner, run_jobs)

    steered = [slice for slice in tmdb.sliced() if "with_genres" in slice or "with_people" in slice]
    assert steered, "no discover slice was steered at anything"
    assert "/movie/2000/similar" in tmdb.paths()
    assert "/movie/2000/recommendations" in tmdb.paths()


async def test_a_tracked_film_is_never_suggested(owner, run_jobs, provider, tmdb):
    """Only untracked films, whatever the sourcing turns up (invariant)."""
    tmdb.with_neighbours(RATED[0].tmdb_id, *CANDIDATES, *RATED)
    provider.will_say(**ranked(*CANDIDATES, *RATED))
    await rating_films(owner, run_jobs)

    films = await visit(owner, run_jobs)

    assert ids(films).isdisjoint({film.tmdb_id for film in RATED})
    assert films, "the shelf should still have the untracked candidates on it"


async def test_a_dismissed_film_is_never_suggested(owner, run_jobs, provider, db):
    """Only undismissed films: the suppression outlives the ticket that writes it."""
    provider.will_say(**ranked(*CANDIDATES))
    # Looked at rather than tracked, which is what puts the film in the shared catalog
    # without giving the owner any relationship to it - the state a dismissal needs.
    await owner.get(f"/api/films/{CANDIDATES[0].tmdb_id}")
    await dismiss(db, uuid.UUID(await account_id(owner)), CANDIDATES[0].tmdb_id)

    await rating_films(owner, run_jobs)
    films = await visit(owner, run_jobs)

    assert CANDIDATES[0].tmdb_id not in ids(films)
    assert films, "dismissing one film should not empty the shelf"


async def test_adding_a_shelved_film_takes_it_off_the_shelf(owner, run_jobs, provider):
    """The invariant holds on the read, not merely on the rebuild.

    A restock runs when the profile version moves, and the owner can add a suggestion to
    their backlog from the film's own page long before that - so the shelf has to stop
    showing it at once rather than when the engine next gets round to noticing.
    """
    provider.will_say(**ranked(CANDIDATES[0], CANDIDATES[1]))
    await rating_films(owner, run_jobs)
    assert ids(await visit(owner, run_jobs)) == {3000, 3001}

    added = await owner.post(f"/api/films/{CANDIDATES[0].tmdb_id}/backlog")
    assert added.status_code == 200, added.text

    assert ids(await shelf(owner, boundary=False)) == {3001}


async def test_a_ruled_out_genre_is_enforced_mechanically(owner, run_jobs, provider, tmdb):
    """A constraint with a structural footprint drops films rather than asking nicely."""
    tmdb.with_neighbours(RATED[0].tmdb_id, *CANDIDATES, SCARY)
    await rating_films(owner, run_jobs)
    await thumb_down(owner, "You would enjoy a horror film.", excludes={"genre": "Horror"})
    provider.will_say(**ranked(SCARY, *CANDIDATES))

    films = await visit(owner, run_jobs)

    assert SCARY.tmdb_id not in ids(films)
    assert films, "the constraint should exclude a genre, not the whole feed"


async def test_a_ruled_out_language_is_enforced_mechanically(owner, run_jobs, provider, tmdb):
    """The other structural footprint taste-profile.md names, on the same lever."""
    tmdb.with_neighbours(RATED[0].tmdb_id, *CANDIDATES, SUBTITLED)
    await rating_films(owner, run_jobs)
    await thumb_down(owner, "You are happy reading subtitles.", excludes={"language": "it"})
    provider.will_say(**ranked(SUBTITLED, *CANDIDATES))

    films = await visit(owner, run_jobs)

    assert SUBTITLED.tmdb_id not in ids(films)
    assert films


async def test_a_prose_only_correction_excludes_nothing(owner, run_jobs, provider, tmdb):
    """Most corrections are about the writing, and a rule is only made where one is named."""
    tmdb.with_neighbours(RATED[0].tmdb_id, *CANDIDATES, SCARY)
    await rating_films(owner, run_jobs)
    await thumb_down(owner, "You do not only watch westerns.")
    provider.will_say(**ranked(SCARY, *CANDIDATES))

    assert SCARY.tmdb_id in ids(await visit(owner, run_jobs))


# --- The rerank ---


@WINDOWED
async def test_the_shortlist_is_reranked_in_windows(owner, run_jobs, provider):
    """Windowed, so one provider timeout cannot take the whole month's budget with it."""
    provider.will_say(**ranked(*CANDIDATES)).will_say(**ranked(*CANDIDATES))
    await rating_films(owner, run_jobs)

    await visit(owner, run_jobs)

    assert len(provider.asked_of(llm.RERANK_SYSTEM)) == 2, "four candidates, windows of two"


@WINDOWED
async def test_a_film_already_judged_at_this_version_skips_the_provider(
    owner, run_jobs, provider, db
):
    """The verdict cache: what a cut-short run bought is never bought again.

    The provider drops out between the two windows, so the first window's films are judged
    and the rest wait. When it comes back, the run that resumes asks about the films that
    waited and about nothing else.
    """
    provider.will_say(**ranked(*CANDIDATES))
    provider.will_fail(llm.ProviderUnavailable("down"), after=1, of=llm.RERANK_SYSTEM)
    account = await rating_films(owner, run_jobs)
    await visit(owner, run_jobs)
    judged = {row[0] for row in await verdicts(db, account)}
    assert judged, "the first window should have landed before the provider went away"

    provider.recovers().will_say(**ranked(*CANDIDATES))
    await visit(owner, run_jobs)

    resumed = provider.asked_of(llm.RERANK_SYSTEM)[-1].prompt.user
    assert all(str(film_id) not in resumed for film_id in judged)


@WINDOWED
async def test_a_poor_fit_is_cached_as_a_negative(owner, run_jobs, provider, db):
    """Never shown, and never re-sent: a rejection is bought once and then kept."""
    provider.will_say(**ranked(*CANDIDATES, fit="poor_fit"))
    provider.will_fail(llm.ProviderUnavailable("down"), after=1, of=llm.RERANK_SYSTEM)
    account = await rating_films(owner, run_jobs)
    await visit(owner, run_jobs)
    rejected = {row[0] for row in await verdicts(db, account)}
    assert rejected, "the first window should have rejected something"

    provider.recovers().will_say(**ranked(*CANDIDATES))
    films = await visit(owner, run_jobs)

    assert rejected.isdisjoint(ids(films)), "a rejected film reached the shelf"
    resumed = provider.asked_of(llm.RERANK_SYSTEM)[-1].prompt.user
    assert all(str(film_id) not in resumed for film_id in rejected), "a rejection was re-bought"


async def test_the_shelf_is_ordered_by_the_rerank(owner, run_jobs, provider):
    """Position is the entire public statement, and the reranker is what makes it."""
    provider.will_say(**ranked(CANDIDATES[1], CANDIDATES[0]))
    await rating_films(owner, run_jobs)

    films = await visit(owner, run_jobs)

    assert [film["tmdb_id"] for film in films] == [3001, 3000]


async def test_a_plausible_film_sits_below_a_strong_one(owner, run_jobs, provider):
    """The bucket orders the shelf and stays off it: the owner sees the order, not the word.

    Scripted the wrong way round deliberately - the merely plausible film is offered
    first - so the assertion is about the bucket outranking the listwise position rather
    than about the answer being copied out.
    """
    provider.will_say(**mixed((CANDIDATES[0], "plausible"), (CANDIDATES[1], "strong_fit")))
    await rating_films(owner, run_jobs)

    films = await visit(owner, run_jobs)

    assert [film["tmdb_id"] for film in films] == [3001, 3000]


@pytest.mark.settings(**{**PIPELINE, "discovery_shortlist": 1}, discovery_rerank_window=10)
async def test_the_prefilter_leans_against_popularity(owner, run_jobs, provider, tmdb):
    """Deep cuts dominate: of two films the fit cannot tell apart, the obscure one gets in.

    Asserted through the one shortlist place on offer rather than by reading a score, so
    what is pinned is the damper's effect and not its arithmetic. The two films carry the
    same genre and the same director as everything the owner rated, so the only thing left
    between them is how many people have seen them.
    """
    known = FilmFixture(3200, "The Famous One", genres=("Western",), vote_count=500_000)
    obscure = FilmFixture(3201, "The Forgotten One", genres=("Western",), vote_count=20)
    tmdb.with_neighbours(RATED[0].tmdb_id, known, obscure)
    provider.will_say(**ranked(known, obscure))
    await rating_films(owner, run_jobs)

    assert ids(await visit(owner, run_jobs)) == {obscure.tmdb_id}


@pytest.mark.settings(**{**PIPELINE, "discovery_shelf": 4}, discovery_rerank_window=2)
async def test_a_rank_means_the_same_thing_in_every_window(owner, run_jobs, provider):
    """Windows are cut from the prefilter's order, so a later window sits below an earlier one.

    A listwise rank is only meaningful inside the list it was made in. If each window's
    ranks began again at zero, the runner-up of the first window would fall behind the
    winner of the last and the prefilter's ordering would be thrown away - so the shelf is
    checked against the windows as they were actually offered, whichever films the
    prefilter put in each.
    """
    provider.will_say(**ranked(*CANDIDATES)).will_say(**ranked(*CANDIDATES))
    await rating_films(owner, run_jobs)

    films = await visit(owner, run_jobs)

    windows = [_offered(asked.prompt.user) for asked in provider.asked_of(llm.RERANK_SYSTEM)]
    assert len(windows) == 2, "the test needs two windows to have anything to say"
    assert [film["tmdb_id"] for film in films] == [
        # Each window in turn, and inside it the order the scripted answer ranked them.
        film.tmdb_id
        for window in windows
        for film in CANDIDATES
        if film.tmdb_id in window
    ]


def _offered(prompt):
    """Which candidates one rerank window was shown, read off the prompt it was shown in."""
    return {film.tmdb_id for film in CANDIDATES if str(film.tmdb_id) in prompt}


# --- The never-pad rule ---


async def test_the_shelf_runs_short_rather_than_pad(owner, run_jobs, provider, db):
    """A thin pipeline shows a thin shelf, with no filler and no apology for it."""
    provider.will_say(**ranked(CANDIDATES[0]))
    account = await rating_films(owner, run_jobs)

    await discovery(owner)
    await run_jobs()
    feed = await discovery(owner)

    assert len(feed["films"]) == 1
    assert set(feed) == {"readiness", "unlocked", "progress", "films"}, "no degraded-mode banner"
    await assert_shelf_stands_on_verdicts(db, account)


async def test_an_unjudged_film_never_reaches_the_shelf(owner, run_jobs, provider, db):
    """One rule keeps every degraded state coherent, this one included."""
    provider.will_fail(llm.ProviderUnavailable("down"), of=llm.RERANK_SYSTEM)
    account = await rating_films(owner, run_jobs)

    films = await visit(owner, run_jobs)

    assert films == []
    assert await verdicts(db, account) == []


async def test_the_card_carries_what_the_screen_draws_and_nothing_else(owner, run_jobs, provider):
    """Poster, title, year, director, genres, the pitch, and the plot. No badges, ever."""
    provider.will_say(**ranked(CANDIDATES[0]))
    await rating_films(owner, run_jobs)

    (card,) = await visit(owner, run_jobs)

    assert card["title"] == "Django"
    assert card["year"] == 1999
    assert card["directors"] == ["Corbucci"]
    assert card["genres"] == ["Western"]
    assert card["poster_path"] and card["overview"] and card["pitch"]
    assert set(card) == {
        "tmdb_id",
        "title",
        "year",
        "poster_path",
        "genres",
        "directors",
        "overview",
        "pitch",
    }


# --- Versions, degradation, and what a visit costs ---


async def test_a_version_bump_appends_verdicts_rather_than_replacing_them(
    owner, run_jobs, provider, db, tmdb
):
    """The bump is the cache invalidation, so it is also the trigger (taste-profile.md)."""
    tmdb.with_films(*MORE)
    provider.will_say(**ranked(CANDIDATES[0], CANDIDATES[1]))
    account = await rating_films(owner, run_jobs)
    await visit(owner, run_jobs)
    assert len(await prose_versions(db, account)) == 1

    provider.will_say(**ranked(CANDIDATES[1], CANDIDATES[0]))
    await build_ordering(owner, MORE, band=5.0)
    await run_jobs()

    assert len(await prose_versions(db, account)) == 2
    assert {row[1] for row in await verdicts(db, account)} == {1, 2}


async def test_a_stale_verdict_stays_usable_when_the_provider_is_gone(
    owner, run_jobs, provider, db, tmdb
):
    """Degraded is not empty: last version's judgment is still a judgment.

    The films the reranker judged against version one stay on the shelf after the bump,
    ordered by the linear scorer, because a stale verdict is the honest thing Anchor has
    and throwing it away would leave the owner with nothing rather than with less.
    """
    tmdb.with_films(*MORE)
    provider.will_say(**ranked(CANDIDATES[0], CANDIDATES[1]))
    account = await rating_films(owner, run_jobs)
    before = ids(await visit(owner, run_jobs))
    assert before == {3000, 3001}

    provider.will_fail(llm.ProviderUnavailable("down"), of=llm.RERANK_SYSTEM)
    await build_ordering(owner, MORE, band=5.0)
    await run_jobs()
    after = ids(await visit(owner, run_jobs))

    assert after == before
    assert {row[1] for row in await verdicts(db, account)} == {1}, "nothing new was judged"


async def test_a_second_visit_at_one_version_buys_nothing(owner, run_jobs, provider, db):
    """The restock is idempotent per version, so re-reading the screen is free."""
    provider.will_say(**ranked(*CANDIDATES))
    account = await rating_films(owner, run_jobs)
    await visit(owner, run_jobs)
    spent = len(await spend_ledger(db, account))

    await visit(owner, run_jobs)

    assert len(await spend_ledger(db, account)) == spent


async def test_reloading_after_an_action_queues_no_work(owner, run_jobs, provider, db, tmdb):
    """Engine-driven changes land at arrivals; a reload is the same session (discovery.md).

    The version is bumped and left unrestocked, so there is real work waiting: a reload
    that queued it would be the shelf changing under the owner's own action.
    """
    tmdb.with_films(*MORE)
    account = await rating_films(owner, run_jobs)
    await build_ordering(owner, MORE, band=5.0)
    await run_jobs()
    assert len(await prose_versions(db, account)) == 2
    bought = len(provider.asked_of(llm.RERANK_SYSTEM))

    await discovery(owner, boundary=False)
    await run_jobs()

    assert len(provider.asked_of(llm.RERANK_SYSTEM)) == bought


async def test_one_account_never_sees_another_shelf(owner, other_owner, run_jobs, provider):
    """Every account-realm row is owner-scoped, the shelf included."""
    provider.will_say(**ranked(*CANDIDATES))
    await rating_films(owner, run_jobs)
    assert await visit(owner, run_jobs)

    assert await shelf(other_owner) == []
