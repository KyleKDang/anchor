"""The numeric taste profile as the owner's flows produce it: retrain, artifacts, readiness.

These read as what the owner did - place films, designate an anchor, open Profile - and
assert what that leaves behind: a queued retrain riding the placement's own transaction,
a regenerated weight vector and exemplar set, one appended metrics row, and a readiness
state derived fresh on every read. The fit's quality is not asserted here; that is the
trainer's own seam (test_trainer.py), because scorer quality is not an API behavior.
"""

import uuid

import numpy as np
import pytest
from sqlalchemy import select

from anchor import features, jobs, trainer
from anchor.models import Film
from faketmdb import FilmFixture
from flows import (
    LIBRARY,
    account_id,
    add_to_backlog,
    backlog,
    build_ordering,
    designate,
    film_page,
    mark_watched,
    place,
    profile,
    retire,
    stage_of,
)
from flows import answer as answer_comparison
from flows import begin as begin_placement
from invariants import (
    assert_nothing_rating_shaped,
    assert_readiness_not_stored,
    exemplars,
    ordering_snapshot,
    taste_metrics,
    weight_vector,
)

UNSEEN = FilmFixture(3001, "Never Rated", release_date="2011-01-01", genres=("Drama", "Western"))


@pytest.fixture(autouse=True)
def stocked(tmdb):
    return tmdb.with_films(*LIBRARY, UNSEEN)


async def owner_id(client):
    return uuid.UUID(await account_id(client))


async def queued_retrains(jobs_app):
    return [
        job
        for job in await jobs_app.job_manager.list_jobs_async()
        if job.status == "todo" and job.task_name == jobs.task_name(jobs.retrain_taste_profile)
    ]


# --- The retrain rides the change that made it necessary ---


async def test_a_landed_placement_queues_a_retrain(owner, jobs_app):
    await place(owner, LIBRARY[0], "b")

    assert len(await queued_retrains(jobs_app)) == 1


async def test_a_comparison_that_moves_nothing_queues_nothing(owner, jobs_app):
    """An answer is evidence. Until it settles the search, the ordering has not moved."""
    await build_ordering(owner, LIBRARY[:4])
    before = len(await queued_retrains(jobs_app))

    await mark_watched(owner, LIBRARY[4], "now")
    opened = await begin_placement(owner, LIBRARY[4])
    stepped = await answer_comparison(owner, LIBRARY[4], opened["b"]["tmdb_id"], "b")

    assert not stepped["done"], "the premise: this placement has not landed yet"
    assert len(await queued_retrains(jobs_app)) == before


async def test_designating_and_retiring_an_anchor_each_queue_a_retrain(owner, jobs_app):
    """Both change the exemplar set, which is half of what a retrain regenerates."""
    await build_ordering(owner, LIBRARY[:4])
    before = len(await queued_retrains(jobs_app))

    await designate(owner, 4.0, LIBRARY[1])
    await retire(owner, 4.0)

    assert len(await queued_retrains(jobs_app)) == before + 2


async def test_the_retrain_regenerates_the_whole_profile(owner, db, jobs_app, run_jobs):
    await build_ordering(owner, LIBRARY[:6])
    await designate(owner, 4.0, LIBRARY[2])
    account = await owner_id(owner)
    assert await weight_vector(db, account) is None, "the premise: no retrain has run yet"

    await run_jobs()

    stored = await weight_vector(db, account)
    assert stored is not None and stored["weights"] and stored["training_pairs"] > 0
    assert await exemplars(db, account)
    assert await taste_metrics(db, account)
    assert await queued_retrains(jobs_app) == []


# --- The exemplar set ---


async def test_the_exemplar_set_is_the_anchors_and_the_ends_of_the_ordering(owner, db, run_jobs):
    await build_ordering(owner, LIBRARY[:8])
    await designate(owner, 4.0, LIBRARY[2])
    await run_jobs()

    account = await owner_id(owner)
    ranked = [slot[0] for slot in await ordering_snapshot(db, account)]
    rows = await exemplars(db, account)

    assert [(role, film) for role, _, _, film in rows if role == "best"] == [
        ("best", film) for film in ranked[:3]
    ]
    assert [(role, film) for role, _, _, film in rows if role == "worst"] == [
        ("worst", film) for film in reversed(ranked[-3:])
    ]
    assert [(band, film) for role, band, _, film in rows if role == "anchor"] == [
        (4.0, LIBRARY[2].tmdb_id)
    ]


async def test_the_exemplar_set_follows_the_anchors_as_they_change(owner, db, run_jobs):
    """Recomputed mechanically: a retired anchor stops standing for the owner's taste."""
    await build_ordering(owner, LIBRARY[:6])
    await designate(owner, 4.0, LIBRARY[2])
    await run_jobs()
    account = await owner_id(owner)
    assert any(role == "anchor" for role, _, _, _ in await exemplars(db, account))

    await retire(owner, 4.0)
    await run_jobs()

    assert not any(role == "anchor" for role, _, _, _ in await exemplars(db, account))


async def test_the_exemplar_set_follows_the_ordering_as_it_grows(owner, db, run_jobs):
    await build_ordering(owner, LIBRARY[:6])
    await run_jobs()
    account = await owner_id(owner)
    best_before = [film for role, _, _, film in await exemplars(db, account) if role == "best"]

    # A film better than everything already rated: the top of the ordering changes hands.
    await place(owner, LIBRARY[6], "a")
    await run_jobs()

    best_after = [film for role, _, _, film in await exemplars(db, account) if role == "best"]
    assert best_after[0] == LIBRARY[6].tmdb_id
    assert best_after != best_before


async def test_a_library_too_short_to_have_two_ends_never_uses_a_film_twice(owner, db, run_jobs):
    """With three films, the middle one is not both the best and the worst of anything."""
    await build_ordering(owner, LIBRARY[:3])
    await run_jobs()

    rows = await exemplars(db, await owner_id(owner))
    films = [film for _, _, _, film in rows]
    assert len(films) == len(set(films))


# --- The metrics log ---


async def test_each_retrain_appends_one_metrics_row_and_rewrites_none(owner, db, run_jobs):
    await build_ordering(owner, LIBRARY[:5])
    await run_jobs()
    account = await owner_id(owner)
    before = await taste_metrics(db, account)
    assert len(before) == 5, "one row per landed placement"

    await place(owner, LIBRARY[5], "b")
    await run_jobs()

    after = await taste_metrics(db, account)
    assert after[: len(before)] == before, "a retrain rewrote an earlier metrics row"
    assert len(after) == len(before) + 1


async def test_a_metrics_row_carries_the_counts_that_contextualise_its_accuracy(
    owner, db, run_jobs
):
    """An accuracy with no denominator beside it is a number nobody can read."""
    await build_ordering(owner, LIBRARY[:6])
    await designate(owner, 4.0, LIBRARY[2])
    await run_jobs()

    latest = (await taste_metrics(db, await owner_id(owner)))[-1]
    (_, accuracy, held_out, training, rated, comparisons, settled, spanned, _) = latest
    assert rated == 6
    assert comparisons > 0
    assert settled == 6
    assert spanned == 1
    assert training > 0
    assert accuracy is None or (0.0 <= accuracy <= 1.0 and held_out > 0)


# --- Readiness ---


async def test_a_fresh_account_is_cold_and_says_what_would_change_that(owner):
    payload = await profile(owner)

    assert payload["readiness"] == "cold"
    assert payload["evidence"] == {
        "rated_films": 0,
        "explicit_comparisons": 0,
        "settled_films": 0,
        "explicit_share": 0.0,
        "bands_spanned": 0,
    }
    forming = stage_of(payload, "forming")
    assert not forming["reached"]
    assert {bar["dimension"] for bar in forming["thresholds"]} == {
        "rated_films",
        "bands_spanned",
    }
    assert all(bar["have"] < bar["need"] for bar in forming["thresholds"])


@pytest.mark.settings(readiness_forming_films=4, readiness_forming_bands=1)
async def test_evidence_carries_an_account_into_forming(owner):
    await build_ordering(owner, LIBRARY[:5])
    assert (await profile(owner))["readiness"] == "cold", "no band structure yet"

    await designate(owner, 4.0, LIBRARY[2])

    payload = await profile(owner)
    assert payload["readiness"] == "forming"
    assert stage_of(payload, "forming")["reached"]
    assert not stage_of(payload, "ready")["reached"]


@pytest.mark.settings(
    readiness_forming_films=4,
    readiness_forming_bands=1,
    readiness_ready_films=5,
    readiness_ready_explicit_share=0.5,
)
async def test_a_settled_library_reaches_ready(owner):
    await build_ordering(owner, LIBRARY[:5])
    await designate(owner, 4.0, LIBRARY[2])

    payload = await profile(owner)
    assert payload["readiness"] == "ready"
    assert all(stage["reached"] for stage in payload["stages"])
    assert payload["evidence"]["explicit_share"] == 1.0


@pytest.mark.settings(readiness_forming_films=4, readiness_forming_bands=1)
async def test_readiness_moves_with_the_evidence_before_any_job_has_run(owner, db):
    """Derived on read, so the screen never waits on - or reads - the worker's artifacts."""
    await build_ordering(owner, LIBRARY[:5])
    await designate(owner, 4.0, LIBRARY[2])

    assert await weight_vector(db, await owner_id(owner)) is None
    assert (await profile(owner))["readiness"] == "forming"


async def test_readiness_is_stored_nowhere(db):
    await assert_readiness_not_stored(db)


async def test_one_account_readiness_never_reads_another_evidence(owner, other_owner):
    await build_ordering(owner, LIBRARY[:5])

    assert (await profile(other_owner))["evidence"]["rated_films"] == 0


# --- ADR 0005 ---


async def test_a_trained_vector_never_reaches_a_film_the_owner_has_not_watched(owner, run_jobs):
    """The vector can score every one of these, and none of that may cross the boundary."""
    await build_ordering(owner, LIBRARY[:6])
    await add_to_backlog(owner, UNSEEN)
    await run_jobs()

    assert_nothing_rating_shaped(await backlog(owner), "the backlog")
    assert_nothing_rating_shaped(await film_page(owner, UNSEEN), "an unwatched film page")
    assert_nothing_rating_shaped(await profile(owner), "the profile screen")


# --- The artifact is usable ---


async def test_the_stored_vector_scores_a_film_the_owner_has_never_rated(owner, db, run_jobs):
    """Scoring unseen films by construction is why ADR 0004 chose this scorer at all."""
    await build_ordering(owner, LIBRARY[:6])
    await add_to_backlog(owner, UNSEEN)
    await run_jobs()

    stored = await weight_vector(db, await owner_id(owner))
    assert stored is not None
    space = features.FeatureSpace.from_json(stored["space"])
    weights = np.array([stored["weights"][column] for column in space.columns])
    async with db.sessions() as session:
        unseen = await session.scalar(select(Film).where(Film.tmdb_id == UNSEEN.tmdb_id))

    assert isinstance(trainer.score(weights, space, unseen), float)
