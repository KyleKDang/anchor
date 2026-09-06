"""The numeric taste profile as the owner's flows produce it: retrain, artifacts, readiness.

These read as what the owner did - rate films, mark an anchor, open Profile - and assert
what that leaves behind: a queued retrain riding the rating's own transaction, a
regenerated weight vector and exemplar set, one appended metrics row, and a readiness
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
    film_page,
    mark_anchor,
    profile,
    rate,
    retire_anchor,
    scale,
    stage_of,
)
from invariants import (
    assert_nothing_rating_shaped,
    assert_readiness_not_stored,
    exemplars,
    ordering_snapshot,
    taste_metrics,
    trained_at,
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


async def test_a_rating_queues_a_retrain(owner, jobs_app):
    await rate(owner, LIBRARY[0], 4.0)

    assert len(await queued_retrains(jobs_app)) == 1


async def test_marking_and_retiring_an_anchor_each_queue_a_retrain(owner, jobs_app):
    """Both change the exemplar set, which is half of what a retrain regenerates."""
    await build_ordering(owner, LIBRARY[:4])
    before = len(await queued_retrains(jobs_app))

    await mark_anchor(owner, LIBRARY[1])
    await retire_anchor(owner, LIBRARY[1])

    assert len(await queued_retrains(jobs_app)) == before + 2


async def test_a_toggle_that_changes_nothing_queues_nothing(owner, jobs_app):
    """Marking an already-marked film says nothing new, so nothing is regenerated."""
    await rate(owner, LIBRARY[0], 4.0)
    await mark_anchor(owner, LIBRARY[0])
    before = len(await queued_retrains(jobs_app))

    await mark_anchor(owner, LIBRARY[0])

    assert len(await queued_retrains(jobs_app)) == before


async def test_the_retrain_regenerates_the_whole_profile(owner, db, jobs_app, run_jobs):
    await scale(owner, size=6)
    await mark_anchor(owner, LIBRARY[2])
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
    await scale(owner, size=8)
    await mark_anchor(owner, LIBRARY[2])
    await run_jobs()

    account = await owner_id(owner)
    wall = await ordering_snapshot(db, account)
    ranked = [film_id for band in sorted(wall, reverse=True) for film_id in wall[band]]
    rows = await exemplars(db, account)

    assert [(role, film) for role, _, _, film in rows if role == "best"] == [
        ("best", film) for film in ranked[:3]
    ]
    assert [(role, film) for role, _, _, film in rows if role == "worst"] == [
        ("worst", film) for film in reversed(ranked[-3:])
    ]
    assert [film for role, _, _, film in rows if role == "anchor"] == [LIBRARY[2].tmdb_id]


async def test_a_large_pool_is_capped_most_recently_marked_first(owner, db, run_jobs):
    """ "A few per band" (taste-profile.md): a prompt carries a handful, not a hundred."""
    await build_ordering(owner, LIBRARY[:5], band=4.0)
    for film in LIBRARY[:5]:
        await mark_anchor(owner, film)
    await run_jobs()

    anchored = [
        film for role, _, _, film in await exemplars(db, await owner_id(owner)) if role == "anchor"
    ]

    assert anchored == [film.tmdb_id for film in reversed(LIBRARY[2:5])]


async def test_the_exemplar_set_follows_the_anchors_as_they_change(owner, db, run_jobs):
    """Recomputed mechanically: a retired anchor stops standing for the owner's taste."""
    await scale(owner, size=6)
    await mark_anchor(owner, LIBRARY[2])
    await run_jobs()
    account = await owner_id(owner)
    assert any(role == "anchor" for role, _, _, _ in await exemplars(db, account))

    await retire_anchor(owner, LIBRARY[2])
    await run_jobs()

    assert not any(role == "anchor" for role, _, _, _ in await exemplars(db, account))


async def test_the_exemplar_set_follows_the_ordering_as_it_grows(owner, db, run_jobs):
    await scale(owner, size=6)
    await run_jobs()
    account = await owner_id(owner)
    best_before = [film for role, _, _, film in await exemplars(db, account) if role == "best"]

    # A film better than everything already rated: the top of the ordering changes hands.
    await rate(owner, LIBRARY[6], 5.0)
    await run_jobs()

    best_after = [film for role, _, _, film in await exemplars(db, account) if role == "best"]
    assert LIBRARY[6].tmdb_id in best_after
    assert best_after != best_before


async def test_a_library_too_short_to_have_two_ends_never_uses_a_film_twice(owner, db, run_jobs):
    """With three films, the middle one is not both the best and the worst of anything."""
    await scale(owner, size=3)
    await run_jobs()

    rows = await exemplars(db, await owner_id(owner))
    films = [film for _, _, _, film in rows]
    assert len(films) == len(set(films))


async def test_the_trained_at_marker_moves_with_every_retrain(owner, db, run_jobs):
    """Current-only means one row overwritten, and a marker that never moves says nothing."""
    await scale(owner, size=4)
    await run_jobs()
    account = await owner_id(owner)
    first = await trained_at(db, account)

    await rate(owner, LIBRARY[4], 2.0)
    await run_jobs()

    assert await trained_at(db, account) > first


# --- The metrics log ---


async def test_each_retrain_appends_one_metrics_row_and_rewrites_none(owner, db, run_jobs):
    await scale(owner, size=5)
    await run_jobs()
    account = await owner_id(owner)
    before = await taste_metrics(db, account)
    assert len(before) == 5, "one row per rating"

    await rate(owner, LIBRARY[5], 2.0)
    await run_jobs()

    after = await taste_metrics(db, account)
    assert after[: len(before)] == before, "a retrain rewrote an earlier metrics row"
    assert len(after) == len(before) + 1


async def test_a_metrics_row_carries_the_counts_that_contextualise_its_accuracy(
    owner, db, run_jobs
):
    """An accuracy with no denominator beside it is a number nobody can read."""
    await scale(owner, size=6)
    await run_jobs()

    latest = (await taste_metrics(db, await owner_id(owner)))[-1]
    (_, accuracy, held_out, training, rated, spanned, comparisons, _) = latest
    assert rated == 6
    assert spanned == 3
    assert comparisons == 0, "the minimal picker asks none yet"
    assert training > 0
    assert accuracy is None or (0.0 <= accuracy <= 1.0 and held_out > 0)


async def test_the_metrics_row_counts_the_fit_its_accuracy_came_from(owner, db, run_jobs):
    """Held-out and training partition the evidence; the stored vector carries its own count.

    An accuracy reported beside a count that includes the pairs it was measured on would
    overstate what it was earned from.
    """
    await scale(owner, size=8)
    await run_jobs()
    account = await owner_id(owner)

    (_, _, held_out, training, *_) = (await taste_metrics(db, account))[-1]
    stored = await weight_vector(db, account)
    assert stored is not None
    assert held_out + training == stored["training_pairs"]


# --- Readiness ---


async def test_a_fresh_account_is_cold_and_says_what_would_change_that(owner):
    payload = await profile(owner)

    assert payload["readiness"] == "cold"
    assert payload["evidence"] == {"rated_films": 0, "bands_spanned": 0}
    forming = stage_of(payload, "forming")
    assert not forming["reached"]
    assert {bar["dimension"] for bar in forming["thresholds"]} == {
        "rated_films",
        "bands_spanned",
    }
    assert all(bar["have"] < bar["need"] for bar in forming["thresholds"])


@pytest.mark.settings(readiness_forming_films=4, readiness_forming_bands=3)
async def test_evidence_carries_an_account_into_forming(owner):
    """Films *and* bands: a library stacked in one band has no spread to fit against."""
    await build_ordering(owner, LIBRARY[:5], band=4.0)
    assert (await profile(owner))["readiness"] == "cold", "five films, but one band"

    await rate(owner, LIBRARY[5], 2.0)
    await rate(owner, LIBRARY[6], 5.0)

    payload = await profile(owner)
    assert payload["readiness"] == "forming"
    assert stage_of(payload, "forming")["reached"]
    assert not stage_of(payload, "ready")["reached"]


@pytest.mark.settings(readiness_forming_films=4, readiness_forming_bands=3, readiness_ready_films=6)
async def test_a_real_library_reaches_ready_without_answering_anything(owner):
    """No comparison bar exists: the ordering is complete the moment a film is rated."""
    await scale(owner, size=6)

    payload = await profile(owner)
    assert payload["readiness"] == "ready"
    assert all(stage["reached"] for stage in payload["stages"])
    assert payload["evidence"] == {"rated_films": 6, "bands_spanned": 3}


@pytest.mark.settings(readiness_forming_films=4, readiness_forming_bands=3, readiness_ready_films=9)
async def test_a_library_with_the_spread_but_not_the_size_is_only_forming(owner):
    await scale(owner, size=6)

    payload = await profile(owner)
    assert payload["readiness"] == "forming"
    bars = {bar["dimension"]: bar for bar in stage_of(payload, "ready")["thresholds"]}
    assert bars["bands_spanned"]["have"] >= bars["bands_spanned"]["need"]
    assert bars["rated_films"]["have"] < bars["rated_films"]["need"]


async def test_the_comparison_bars_are_gone_from_profile(owner):
    """ADR 0013 removed the dimension, so the screen cannot show a bar for it."""
    payload = await profile(owner)

    dimensions = {bar["dimension"] for stage in payload["stages"] for bar in stage["thresholds"]}
    assert dimensions == {"rated_films", "bands_spanned"}


@pytest.mark.settings(readiness_forming_films=4, readiness_forming_bands=3)
async def test_readiness_moves_with_the_evidence_before_any_job_has_run(owner, db):
    """Derived on read, so the screen never waits on - or reads - the worker's artifacts."""
    await scale(owner, size=5)

    assert await weight_vector(db, await owner_id(owner)) is None
    assert (await profile(owner))["readiness"] == "forming"


async def test_readiness_is_stored_nowhere(db):
    await assert_readiness_not_stored(db)


async def test_one_account_readiness_never_reads_another_evidence(owner, other_owner):
    await scale(owner, size=5)

    assert (await profile(other_owner))["evidence"]["rated_films"] == 0


# --- ADR 0005 ---


async def test_a_trained_vector_never_reaches_a_film_the_owner_has_not_watched(owner, run_jobs):
    """The vector can score every one of these, and none of that may cross the boundary."""
    await scale(owner, size=6)
    await add_to_backlog(owner, UNSEEN)
    await run_jobs()

    assert_nothing_rating_shaped(await backlog(owner), "the backlog")
    assert_nothing_rating_shaped(await film_page(owner, UNSEEN), "an unwatched film page")
    assert_nothing_rating_shaped(await profile(owner), "the profile screen")


# --- The artifact is usable ---


async def test_the_stored_vector_scores_a_film_the_owner_has_never_rated(owner, db, run_jobs):
    """Scoring unseen films by construction is why ADR 0004 chose this scorer at all."""
    await scale(owner, size=6)
    await add_to_backlog(owner, UNSEEN)
    await run_jobs()

    stored = await weight_vector(db, await owner_id(owner))
    assert stored is not None
    space = features.FeatureSpace.from_json(stored["space"])
    weights = np.array([stored["weights"][column] for column in space.columns])
    async with db.sessions() as session:
        unseen = await session.scalar(select(Film).where(Film.tmdb_id == UNSEEN.tmdb_id))

    assert isinstance(trainer.score(weights, space, unseen), float)
