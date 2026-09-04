"""Placing a film in the ordering: the watch, the comparisons, and where it lands.

These read as the owner's flows, not as endpoint checks: mark a film watched, answer
A / B / Tied / Skip until it settles, walk away mid-flow and come back. The advisory
opponent picker takes a seed, so a scripted answer sequence lands the same way every
run - but nothing here asserts *which* opponent it picked, because that is the advisory
math's business and the tests must not pin it (testing.md).
"""

import uuid

import pytest
from sqlalchemy import select

from anchor.models import (
    AccountFilm,
    LifecycleState,
    Placement,
    PlacementProvenance,
    PlacementTrust,
    WatchEvent,
)
from flows import (
    LIBRARY,
    account_id,
    answer,
    begin,
    build_ordering,
    mark_watched,
    ordering_of,
    place,
    queue_of,
    rated,
)
from invariants import (
    assert_appended_only,
    assert_no_rating_keys,
    assert_nothing_rating_shaped,
    assert_ordering_well_formed,
    assert_realm_wiped,
    comparison_log,
    ordering_snapshot,
)

FIRST, SECOND, THIRD, FOURTH = LIBRARY[:4]


@pytest.fixture(autouse=True)
def stocked(tmdb):
    return tmdb.with_films(*LIBRARY)


# --- Logging a watch ---


async def test_rating_later_seats_the_film_in_the_rate_later_queue(owner):
    await mark_watched(owner, FIRST, "later")

    assert queue_of(await rated(owner)) == [FIRST.tmdb_id]


async def test_rating_now_seats_the_film_too_so_walking_away_is_always_safe(owner):
    """The owner who says "rate now" and then closes the tab has still watched the film."""
    await build_ordering(owner, [FIRST])

    await mark_watched(owner, SECOND, "now")

    # Not one round trip later, when the flow begins: from the moment the watch is logged.
    assert queue_of(await rated(owner)) == [SECOND.tmdb_id]
    await begin(owner, SECOND)
    assert queue_of(await rated(owner)) == [SECOND.tmdb_id]


async def test_marking_a_film_watched_needs_the_owner_to_choose(owner):
    response = await owner.post(f"/api/films/{FIRST.tmdb_id}/watched")

    assert response.status_code == 422, response.text


async def test_leaving_the_rate_later_queue_never_touches_watched_ness(owner):
    await mark_watched(owner, FIRST, "later")

    response = await owner.delete(f"/api/films/{FIRST.tmdb_id}/rate-later")
    assert response.status_code == 204, response.text

    assert queue_of(await rated(owner)) == []
    film = await owner.get(f"/api/films/{FIRST.tmdb_id}")
    assert film.json()["state"] == "watched_unrated"


async def test_every_watch_event_records_its_standing_and_origin(owner, db):
    await mark_watched(owner, FIRST, "later")
    await mark_watched(owner, SECOND, "now")

    async with db.sessions() as session:
        events = list(await session.scalars(select(WatchEvent).order_by(WatchEvent.watched_at)))
    assert [event.film_id for event in events] == [FIRST.tmdb_id, SECOND.tmdb_id]
    assert {event.standing for event in events} == {"plain_backlog"}
    assert {event.origin for event in events} == {"hand_added"}


# --- The placement flow ---


async def test_the_first_film_lands_without_a_single_comparison(owner, db):
    landed, asked = await place(owner, FIRST, "b")

    assert asked == 0
    assert landed["position"] == 1
    assert landed["total"] == 1
    assert ordering_of(await rated(owner)) == [[FIRST.tmdb_id]]
    await assert_ordering_well_formed(db, await account_id(owner))


async def test_the_better_film_lands_above_the_one_it_beat(owner, db):
    await place(owner, FIRST, "b")

    landed, asked = await place(owner, SECOND, "a")

    assert asked == 1
    assert landed["position"] == 1
    assert ordering_of(await rated(owner)) == [[SECOND.tmdb_id], [FIRST.tmdb_id]]
    await assert_ordering_well_formed(db, await account_id(owner))


async def test_placement_bisects_the_ordering_rather_than_walking_it(owner):
    await build_ordering(owner, LIBRARY[:7])

    landed, asked = await place(owner, LIBRARY[7], "b")

    # Seven slots, so a bisection settles in three questions; a walk would take seven.
    assert asked == 3
    assert landed["position"] == 8


async def test_a_film_lands_where_its_own_answers_put_it(owner, db):
    await build_ordering(owner, LIBRARY[:4])
    ordering = [film.tmdb_id for film in LIBRARY[:4]]

    # Better than the bottom two, worse than the top two: the third slot, whichever
    # opponents the advisory picker happened to offer along the way.
    await mark_watched(owner, LIBRARY[4], "now")
    step = await begin(owner, LIBRARY[4])
    while not step["done"]:
        opponent = step["b"]["tmdb_id"]
        verdict = "a" if ordering.index(opponent) >= 2 else "b"
        step = await answer(owner, LIBRARY[4], opponent, verdict)

    assert step["position"] == 3
    assert ordering_of(await rated(owner)) == [
        [ordering[0]],
        [ordering[1]],
        [LIBRARY[4].tmdb_id],
        [ordering[2]],
        [ordering[3]],
    ]
    await assert_ordering_well_formed(db, await account_id(owner))


async def test_tied_joins_the_opponents_tie_group_and_ends_the_search(owner, db):
    await build_ordering(owner, LIBRARY[:4])

    await mark_watched(owner, LIBRARY[4], "now")
    step = await begin(owner, LIBRARY[4])
    opponent = step["b"]["tmdb_id"]
    landed = await answer(owner, LIBRARY[4], opponent, "tied")

    assert landed["done"] is True
    assert [film["tmdb_id"] for film in landed["neighbours"]["tied_with"]] == [opponent]
    slots = ordering_of(await rated(owner))
    assert sorted(slots[landed["position"] - 1]) == sorted([opponent, LIBRARY[4].tmdb_id])
    assert len(slots) == 4, "a tie joins a slot rather than opening one"
    await assert_ordering_well_formed(db, await account_id(owner))


async def test_skip_records_no_judgment_and_swaps_in_another_opponent(owner, db):
    await build_ordering(owner, LIBRARY[:4])
    account = await account_id(owner)

    await mark_watched(owner, LIBRARY[4], "now")
    first = await begin(owner, LIBRARY[4])
    second = await answer(owner, LIBRARY[4], first["b"]["tmdb_id"], "skip")

    assert second["done"] is False
    assert second["b"]["tmdb_id"] != first["b"]["tmdb_id"]
    assert second["answered"] == 0, "a skip is not a judgment"
    log = [entry for entry in await comparison_log(db, account) if entry[2] == LIBRARY[4].tmdb_id]
    assert [entry[5] for entry in log] == ["skip"]


async def test_skipping_every_opponent_lands_the_film_but_trusts_it_less(owner, db):
    await build_ordering(owner, LIBRARY[:4])

    await mark_watched(owner, LIBRARY[4], "now")
    step = await begin(owner, LIBRARY[4])
    skipped = set()
    while not step["done"] and step["b"]["tmdb_id"] not in skipped:
        skipped.add(step["b"]["tmdb_id"])
        step = await answer(owner, LIBRARY[4], step["b"]["tmdb_id"], "skip")

    # Skipping every film in range leaves no question to ask, so the flow lands the film
    # rather than dead-ending on the owner...
    assert step["done"] is True
    assert len(skipped) == 4

    # ...but no answer picked that spot, so it is not a settled judgment and must not be
    # stamped as one, or graduation would never come back to it.
    async with db.sessions() as session:
        placement = await session.scalar(
            select(Placement)
            .join(AccountFilm, AccountFilm.id == Placement.account_film_id)
            .where(AccountFilm.film_id == LIBRARY[4].tmdb_id)
        )
    assert placement is not None
    assert placement.provenance is PlacementProvenance.early_bail
    assert placement.trust is PlacementTrust.provisional


async def test_a_placement_the_owner_answered_through_is_fully_trusted(owner, db):
    await build_ordering(owner, LIBRARY[:4])

    await place(owner, LIBRARY[4], "b")

    async with db.sessions() as session:
        placement = await session.scalar(
            select(Placement)
            .join(AccountFilm, AccountFilm.id == Placement.account_film_id)
            .where(AccountFilm.film_id == LIBRARY[4].tmdb_id)
        )
    assert placement is not None
    assert placement.provenance is PlacementProvenance.completed
    assert placement.trust is PlacementTrust.full


# --- Abandoning and resuming ---


async def test_abandoning_mid_flow_leaves_the_film_watched_unrated_and_queued(owner, db):
    await build_ordering(owner, LIBRARY[:4])

    await mark_watched(owner, LIBRARY[4], "now")
    step = await begin(owner, LIBRARY[4])
    await answer(owner, LIBRARY[4], step["b"]["tmdb_id"], "b")
    # ...and the owner closes the tab. Nothing signals that; nothing has to.

    assert queue_of(await rated(owner)) == [LIBRARY[4].tmdb_id]
    async with db.sessions() as session:
        state = await session.scalar(
            select(AccountFilm.state).where(AccountFilm.film_id == LIBRARY[4].tmdb_id)
        )
    assert state is LifecycleState.watched_unrated


async def test_a_later_attempt_resumes_from_the_answers_already_given(owner, db):
    await build_ordering(owner, LIBRARY[:7])

    await mark_watched(owner, LIBRARY[7], "now")
    step = await begin(owner, LIBRARY[7])
    await answer(owner, LIBRARY[7], step["b"]["tmdb_id"], "b")

    resumed = await begin(owner, LIBRARY[7])
    assert resumed["done"] is False
    assert resumed["answered"] == 1, "the first attempt's answer still counts"

    asked = 1
    while not resumed["done"]:
        asked += 1
        resumed = await answer(owner, LIBRARY[7], resumed["b"]["tmdb_id"], "b")

    # Three questions in total, exactly as one uninterrupted run would have taken.
    assert asked == 3
    assert resumed["position"] == 8
    await assert_ordering_well_formed(db, await account_id(owner))


async def test_a_stale_answer_is_refused_rather_than_appended(owner, db):
    await build_ordering(owner, LIBRARY[:4])
    account = await account_id(owner)

    await mark_watched(owner, LIBRARY[4], "now")
    step = await begin(owner, LIBRARY[4])
    # Say the best film beat the new one, then answer about that same film again: the
    # bounds have moved past it, so the second answer is about a question long gone.
    await answer(owner, LIBRARY[4], LIBRARY[0].tmdb_id, "b")
    before = await comparison_log(db, account)

    response = await owner.post(
        f"/api/placements/{LIBRARY[4].tmdb_id}/answers",
        json={
            "a_tmdb_id": LIBRARY[4].tmdb_id,
            "b_tmdb_id": LIBRARY[0].tmdb_id,
            "verdict": "a",
        },
    )

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "stale_question"
    assert await comparison_log(db, account) == before
    assert step["done"] is False


async def test_placing_a_film_the_owner_has_not_watched_is_refused(owner):
    await owner.post(f"/api/films/{FIRST.tmdb_id}/backlog")

    response = await owner.post(f"/api/placements/{FIRST.tmdb_id}")

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "not_watched"


# --- The invariants ---


async def test_the_comparison_log_is_append_only_across_every_flow(owner, db):
    account = await account_id(owner)
    await build_ordering(owner, LIBRARY[:4])
    before = await comparison_log(db, account)
    assert before, "placing four films records judgments"

    await place(owner, LIBRARY[4], "a")
    await mark_watched(owner, LIBRARY[5], "later")
    await owner.delete(f"/api/films/{LIBRARY[5].tmdb_id}/rate-later")

    after = await comparison_log(db, account)
    assert_appended_only(before, after, "placing another film")
    assert len(after) > len(before)


async def test_every_logged_judgment_carries_its_films_verdict_context_and_status(owner, db):
    await build_ordering(owner, [FIRST])
    await place(owner, SECOND, "a")

    [entry] = await comparison_log(db, await account_id(owner))
    _id, kind, subject, film_a, film_b, verdict, context, status, created_at = entry
    assert (kind, subject, film_a, film_b) == (
        "overall",
        SECOND.tmdb_id,
        SECOND.tmdb_id,
        FIRST.tmdb_id,
    )
    assert (verdict, context, status) == ("a", "placement", "active")
    assert created_at is not None


async def test_nothing_but_the_owners_answers_moves_the_ordering(owner, db, run_jobs):
    await build_ordering(owner, LIBRARY[:4])
    account = await account_id(owner)
    before = await ordering_snapshot(db, account)

    # Everything the owner can do that is not an answer.
    await owner.get("/api/films/search", params={"query": "Film"})
    await owner.post(f"/api/films/{LIBRARY[5].tmdb_id}/backlog")
    await owner.get(f"/api/films/{LIBRARY[6].tmdb_id}")
    await mark_watched(owner, LIBRARY[7], "later")
    await owner.delete(f"/api/films/{LIBRARY[7].tmdb_id}/rate-later")
    await owner.get("/api/watchlist/backlog")
    await run_jobs()

    assert await ordering_snapshot(db, account) == before
    await assert_ordering_well_formed(db, account)


async def test_one_account_never_sees_anothers_ordering(owner, other_owner, db):
    await build_ordering(owner, LIBRARY[:3])
    await build_ordering(other_owner, [LIBRARY[5]])

    assert ordering_of(await rated(other_owner)) == [[LIBRARY[5].tmdb_id]]
    await assert_ordering_well_formed(db, await account_id(owner))
    await assert_ordering_well_formed(db, await account_id(other_owner))


# --- What the screens show ---


async def test_the_rated_screen_shows_the_ordering_and_the_queue_below_it(owner):
    await build_ordering(owner, LIBRARY[:3])
    await mark_watched(owner, LIBRARY[4], "later")

    payload = await rated(owner)

    assert ordering_of(payload) == [[film.tmdb_id] for film in LIBRARY[:3]]
    assert [
        film["position"] for group in payload["groups"] for slot in group["slots"] for film in slot
    ] == [1, 2, 3]
    assert queue_of(payload) == [LIBRARY[4].tmdb_id]
    assert_nothing_rating_shaped(payload, "the Rated screen before any divider is pinned")


async def test_the_done_screen_shows_the_landed_position_with_its_neighbours(owner):
    await build_ordering(owner, LIBRARY[:4])
    ordering = [film.tmdb_id for film in LIBRARY[:4]]

    await mark_watched(owner, LIBRARY[4], "now")
    step = await begin(owner, LIBRARY[4])
    while not step["done"]:
        opponent = step["b"]["tmdb_id"]
        step = await answer(
            owner, LIBRARY[4], opponent, "a" if ordering.index(opponent) >= 2 else "b"
        )

    assert (step["position"], step["total"]) == (3, 5)
    assert [film["tmdb_id"] for film in step["neighbours"]["above"]] == [ordering[1]]
    assert [film["tmdb_id"] for film in step["neighbours"]["below"]] == [ordering[2]]
    assert step["neighbours"]["tied_with"] == []
    # Bands arrive with #28, so the landed film honestly has no value to show yet.
    assert step["rating"] is None


async def test_no_rating_shaped_data_reaches_a_mid_flow_question(owner):
    await build_ordering(owner, LIBRARY[:4])

    await mark_watched(owner, LIBRARY[4], "now")
    step = await begin(owner, LIBRARY[4])

    assert step["done"] is False
    assert_no_rating_keys(step, "a placement question")
    assert set(step["a"]) == {"tmdb_id", "title", "year", "poster_path", "overview"}


async def test_reopening_a_placed_film_shows_where_it_landed(owner):
    await build_ordering(owner, LIBRARY[:2])

    reopened = await begin(owner, LIBRARY[1])

    assert reopened["done"] is True
    assert reopened["position"] == 2


async def test_the_account_realm_wipe_takes_the_ordering_and_the_log_with_it(owner, db):
    """The one exception to the log's never-deleted rule (data-model.md)."""
    account = await account_id(owner)
    await build_ordering(owner, LIBRARY[:3])
    await mark_watched(owner, LIBRARY[4], "later")
    assert await comparison_log(db, account)

    response = await owner.request(
        "DELETE", "/api/account", json={"password": "correct horse battery staple"}
    )

    assert response.status_code == 204, response.text
    await assert_realm_wiped(db, uuid.UUID(account))
