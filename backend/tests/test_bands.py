"""Bands, anchors, and dividers: how a position in the ordering becomes half-stars.

These read as the owner's flows - designate an exemplar, answer a sliver question, stop
early, keep comparing, re-place - and assert what the owner can see: the stars on the
Rated screen, the questions the placement flow puts, the anchors that survive. What the
advisory math picked along the way is deliberately never asserted (testing.md).

The scale most tests build is five films with the middle ones anchored, which is the
smallest structure that has all three interesting places in it: a band with an anchor,
a band with only a stand-in, and the runs at either end where the dividers run out and
a film honestly has no rating at all.
"""

import pytest

from faketmdb import FilmFixture
from flows import (
    LIBRARY,
    account_id,
    anchors,
    answer,
    answer_band,
    bail,
    bands_of,
    begin,
    build_ordering,
    designate,
    film_page,
    keep_comparing,
    mark_watched,
    ordering_of,
    place,
    place_at,
    rated,
    replace_at,
    retire,
)
from invariants import anchors as anchor_rows
from invariants import (
    assert_bands_derived,
    assert_bands_well_formed,
    assert_no_rating_keys,
    assert_nothing_rating_shaped,
    assert_ordering_well_formed,
    comparison_log,
    dividers,
    ordering_snapshot,
)

WESTERN = FilmFixture(2001, "A Western", release_date="2004-01-01", genres=("Western",))
COMEDY = FilmFixture(2002, "A Comedy", release_date="1975-01-01", genres=("Comedy",))


@pytest.fixture(autouse=True)
def stocked(tmdb):
    return tmdb.with_films(*LIBRARY, WESTERN, COMEDY)


async def scale(client, size=5, top=1, bottom=3):
    """An ordering of ``size`` films with a 4.0 and a 3.0 anchor inside it.

    The bands fall out of the two designations: the anchors are their own bands, the
    films between them derive into 3.5, and the films above and below have no rating at
    all, because the dividers that would decide them are still unpinned.
    """
    films = LIBRARY[:size]
    await build_ordering(client, films)
    await designate(client, 4.0, films[top])
    await designate(client, 3.0, films[bottom])
    return [film.tmdb_id for film in films]


# --- Designating an anchor ---


async def test_designating_a_rated_film_makes_it_its_bands_canonical_exemplar(owner, db):
    await build_ordering(owner, LIBRARY[:3])

    result = await designate(owner, 4.0, LIBRARY[1])

    assert result["outcome"] == "designated"
    assert result["film"]["tmdb_id"] == LIBRARY[1].tmdb_id
    assert result["retired"] is None
    listed = {entry["band"]: entry["film"] for entry in (await anchors(owner))["anchors"]}
    assert listed[4.0]["tmdb_id"] == LIBRARY[1].tmdb_id
    assert listed[3.0] is None
    await assert_bands_well_formed(db, await account_id(owner))


async def test_the_anchors_screen_offers_every_band_including_the_empty_ones(owner):
    payload = await anchors(owner)

    assert [entry["band"] for entry in payload["anchors"]] == [
        5.0,
        4.5,
        4.0,
        3.5,
        3.0,
        2.5,
        2.0,
        1.5,
        1.0,
        0.5,
    ]
    assert all(entry["film"] is None for entry in payload["anchors"])


async def test_a_band_holds_one_anchor_at_a_time_so_designating_again_retires_the_old(owner, db):
    ids = await scale(owner)
    await _second_four(owner, ids)

    result = await designate(owner, 4.0, LIBRARY[5])

    assert result["outcome"] == "designated"
    assert result["retired"]["tmdb_id"] == ids[1]
    assert (await anchor_rows(db, await account_id(owner)))[4.0] == LIBRARY[5].tmdb_id
    await assert_bands_well_formed(db, await account_id(owner))


async def test_the_old_anchor_stays_exactly_where_it_sits_when_it_is_replaced(owner, db):
    ids = await scale(owner)
    await _second_four(owner, ids)
    before = await ordering_snapshot(db, await account_id(owner))

    await designate(owner, 4.0, LIBRARY[5])

    assert await ordering_snapshot(db, await account_id(owner)) == before
    assert bands_of(await rated(owner))[ids[1]] == 4.0, "retired, and still a 4.0 where it sits"


async def test_retiring_an_anchor_changes_no_rating_and_no_divider(owner, db):
    ids = await scale(owner)
    account = await account_id(owner)
    before = (await dividers(db, account), bands_of(await rated(owner)))

    await retire(owner, 4.0)

    assert (await dividers(db, account), bands_of(await rated(owner))) == before
    assert (await anchors(owner))["anchors"][2]["film"] is None
    assert ids[1] in bands_of(await rated(owner)), (
        "the film stays rated, it just stops being canonical"
    )


async def test_designating_a_film_the_owner_has_not_rated_is_refused(owner):
    await mark_watched(owner, LIBRARY[0], "later")

    response = await owner.post("/api/anchors/4.0", json={"tmdb_id": LIBRARY[0].tmdb_id})

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "not_rated"


async def test_a_value_that_is_not_a_half_star_band_is_refused(owner):
    await build_ordering(owner, LIBRARY[:1])

    response = await owner.post("/api/anchors/4.2", json={"tmdb_id": LIBRARY[0].tmdb_id})

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "not_a_band"


async def test_comparisons_never_move_an_anchor(owner, db):
    ids = await scale(owner)
    account = await account_id(owner)
    before = await anchor_rows(db, account)

    # Everything the owner can do that is a comparison: place a film through the band,
    # then keep comparing around it until the extension runs out of questions.
    await place(owner, LIBRARY[5], "b")
    step = await keep_comparing(owner, LIBRARY[5])
    while not step["done"]:
        await answer(owner, LIBRARY[5], step["b"]["tmdb_id"], "b")
        step = await keep_comparing(owner, LIBRARY[5])

    assert await anchor_rows(db, account) == before == {4.0: ids[1], 3.0: ids[3]}
    await assert_bands_well_formed(db, account)


# --- Ratings are derived, never stored ---


async def test_an_ordering_with_no_band_structure_shows_positions_and_no_stars(owner, db):
    await build_ordering(owner, LIBRARY[:3])

    payload = await rated(owner)

    assert [group["band"] for group in payload["groups"]] == [None]
    assert bands_of(payload) == {film.tmdb_id: None for film in LIBRARY[:3]}
    assert_nothing_rating_shaped(payload, "the Rated screen before any divider is pinned")
    await assert_bands_derived(db, await account_id(owner), bands_of(payload))


async def test_designating_the_first_anchor_erects_the_dividers_that_give_it_its_star(owner, db):
    await build_ordering(owner, LIBRARY[:3])
    assert await dividers(db, await account_id(owner)) == {}

    await designate(owner, 4.0, LIBRARY[1])

    derived = bands_of(await rated(owner))
    assert derived[LIBRARY[1].tmdb_id] == 4.0
    # The judgment was about one film, so it is the only one it settled: the films
    # either side of it are still honestly rating-pending.
    assert derived[LIBRARY[0].tmdb_id] is None
    assert derived[LIBRARY[2].tmdb_id] is None
    await assert_bands_derived(db, await account_id(owner), derived)
    await assert_bands_well_formed(db, await account_id(owner))


async def test_a_film_between_two_anchors_derives_the_band_between_them(owner, db):
    ids = await scale(owner)

    derived = bands_of(await rated(owner))

    assert derived == {ids[0]: None, ids[1]: 4.0, ids[2]: 3.5, ids[3]: 3.0, ids[4]: None}
    await assert_bands_derived(db, await account_id(owner), derived)


async def test_the_film_page_shows_the_band_its_position_derives_into(owner, db):
    await scale(owner)

    assert (await film_page(owner, LIBRARY[1]))["rating"] == 4.0
    assert (await film_page(owner, LIBRARY[1]))["anchor"] is True
    assert (await film_page(owner, LIBRARY[2]))["rating"] == 3.5
    assert (await film_page(owner, LIBRARY[2]))["anchor"] is False
    assert (await film_page(owner, LIBRARY[0]))["rating"] is None


async def test_nothing_rating_shaped_reaches_an_unwatched_film(owner):
    await scale(owner)
    await owner.post(f"/api/films/{WESTERN.tmdb_id}/backlog")

    page = await film_page(owner, WESTERN)
    results = await owner.get("/api/films/search", params={"query": "Film"})

    assert_nothing_rating_shaped(page, "the film page of a backlogged film")
    assert page["rating"] is None
    assert results.json()["results"], "the search found the owner's own films"


# --- Dividers ---


async def test_every_divider_position_is_auditable_to_the_judgment_that_set_it(owner, db):
    await scale(owner)

    # assert_bands_well_formed refuses any divider whose pinned_by does not name a
    # band judgment, which is the whole of the auditability rule.
    await assert_bands_well_formed(db, await account_id(owner))
    assert await dividers(db, await account_id(owner)), "designating pinned dividers"


async def test_nothing_but_a_band_judgment_moves_a_divider(owner, db, run_jobs):
    await scale(owner)
    account = await account_id(owner)
    before = await dividers(db, account)

    # Everything the owner can do that is not a band judgment. Placing a film shifts
    # the indices under the dividers, which is renumbering, not a move - so the
    # dividers must still separate the very same films afterwards.
    await owner.get("/api/films/search", params={"query": "Film"})
    await owner.post(f"/api/films/{LIBRARY[6].tmdb_id}/backlog")
    await mark_watched(owner, LIBRARY[7], "later")
    await run_jobs()

    assert await dividers(db, account) == before
    await assert_bands_well_formed(db, account)


async def test_renumbering_under_a_placement_leaves_every_band_saying_the_same_thing(owner, db):
    ids = await scale(owner)
    before = bands_of(await rated(owner))

    # A film landing above everything shifts every slot down one, and every divider
    # with it; nothing it did was a judgment about anybody else's band.
    await place_at(owner, LIBRARY[5], ids, 0)

    after = bands_of(await rated(owner))
    assert {film_id: after[film_id] for film_id in before} == before
    await assert_bands_derived(db, await account_id(owner), after)


# --- The sliver question ---


async def test_a_film_landing_between_two_bands_is_asked_which_one_it_is_in(owner):
    ids = await scale(owner)

    step = await place_at(owner, LIBRARY[5], ids, 2)

    assert step["done"] is False
    assert step["kind"] == "band"
    assert step["sliver"] is True
    assert [option["band"] for option in step["options"]] == [4.0, 3.5]


async def test_a_film_landing_clear_of_the_boundary_is_asked_nothing(owner, db):
    ids = await scale(owner, size=7, top=1, bottom=5)

    step = await place_at(owner, LIBRARY[7], ids, 3)

    assert step["done"] is True, "the position already decides the band, so there is no question"
    assert step["rating"] == 3.5
    await assert_bands_derived(db, await account_id(owner), bands_of(await rated(owner)))


async def test_the_sliver_answer_places_the_film_and_sharpens_the_divider(owner, db):
    ids = await scale(owner)
    account = await account_id(owner)
    before = await dividers(db, account)
    step = await place_at(owner, LIBRARY[5], ids, 2)

    landed = await answer_band(owner, LIBRARY[5], 4.0, step["options"][0]["exemplar"]["tmdb_id"])

    # The film is a 4.0 sitting *below* the canonical 4.0, which is the whole point of
    # an anchor being a centroid rather than a floor (ADR 0002).
    assert landed["rating"] == 4.0
    assert landed["position"] == 3
    after = await dividers(db, account)
    assert after[4.0] > before[4.0], "the answer moved the boundary past the new film"
    assert bands_of(await rated(owner))[ids[1]] == 4.0, "the anchor is still a 4.0"
    await assert_bands_well_formed(db, account)


async def test_answering_the_lower_band_leaves_the_divider_where_it_was(owner, db):
    ids = await scale(owner)
    step = await place_at(owner, LIBRARY[5], ids, 2)

    landed = await answer_band(owner, LIBRARY[5], 3.5, step["options"][1]["exemplar"]["tmdb_id"])

    assert landed["rating"] == 3.5
    assert bands_of(await rated(owner))[ids[1]] == 4.0
    await assert_bands_well_formed(db, await account_id(owner))


async def test_a_band_the_question_did_not_offer_is_refused(owner, db):
    ids = await scale(owner)
    account = await account_id(owner)
    await place_at(owner, LIBRARY[5], ids, 2)
    before = await comparison_log(db, account)

    response = await owner.post(f"/api/placements/{LIBRARY[5].tmdb_id}/band", json={"band": 1.0})

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "band_not_offered"
    assert await comparison_log(db, account) == before


async def test_the_band_question_is_the_one_place_a_flow_names_a_rating(owner):
    ids = await scale(owner)

    await mark_watched(owner, LIBRARY[5], "now")
    first = await begin(owner, LIBRARY[5])
    assert_no_rating_keys(first, "a placement comparison")

    step = await place_at(owner, LIBRARY[5], ids, 2)
    assert step["kind"] == "band"
    assert [option["band"] for option in step["options"]] == [4.0, 3.5]


# --- The fallback ladder ---


async def test_a_band_with_no_anchor_is_asked_about_through_a_stand_in(owner):
    ids = await scale(owner)

    step = await place_at(owner, LIBRARY[5], ids, 2)

    standing_in = next(option for option in step["options"] if option["band"] == 3.5)
    assert standing_in["exemplar"]["tmdb_id"] == ids[2]
    assert (await anchors(owner))["anchors"][3]["film"] is None, "3.5 has no anchor of its own"


async def test_a_band_holding_nothing_degrades_the_question_to_a_plain_band_pick(owner):
    await build_ordering(owner, LIBRARY[:2])
    await designate(owner, 4.0, LIBRARY[0])
    await designate(owner, 3.0, LIBRARY[1])
    ids = [film.tmdb_id for film in LIBRARY[:2]]

    step = await place_at(owner, LIBRARY[5], ids, 1)

    assert step["kind"] == "band"
    assert step["sliver"] is False, "three bands and one of them empty is a pick, not a sliver"
    assert [option["band"] for option in step["options"]] == [4.0, 3.5, 3.0]
    assert next(o for o in step["options"] if o["band"] == 3.5)["exemplar"] is None


async def test_the_plain_pick_places_the_film_in_the_band_the_owner_chose(owner, db):
    await build_ordering(owner, LIBRARY[:2])
    await designate(owner, 4.0, LIBRARY[0])
    await designate(owner, 3.0, LIBRARY[1])
    ids = [film.tmdb_id for film in LIBRARY[:2]]
    await place_at(owner, LIBRARY[5], ids, 1)

    landed = await answer_band(owner, LIBRARY[5], 3.5)

    assert landed["rating"] == 3.5
    await assert_bands_well_formed(db, await account_id(owner))
    await assert_bands_derived(db, await account_id(owner), bands_of(await rated(owner)))


async def test_a_hole_in_the_scale_asks_nothing_and_shows_the_position(owner, db):
    ids = await scale(owner)

    # Above the 4.0 anchor the 5.0/4.5 divider is unpinned, so no answer the owner
    # could give would locate it. The honest result is a position and no stars.
    landed = await place_at(owner, LIBRARY[5], ids, 0)

    assert landed["done"] is True
    assert landed["rating"] is None
    assert landed["position"] == 1
    await assert_bands_derived(db, await account_id(owner), bands_of(await rated(owner)))


# --- The ballpark guess ---


async def test_a_ballpark_guess_opens_the_search_at_the_nearest_anchor(owner):
    ids = await scale(owner)
    await mark_watched(owner, LIBRARY[5], "now")

    near_three = await begin(owner, LIBRARY[5], ballpark=3.0)

    assert near_three["b"]["tmdb_id"] == ids[3]


async def test_a_ballpark_range_seeds_at_the_anchor_nearest_the_range(owner):
    ids = await scale(owner)
    await mark_watched(owner, LIBRARY[5], "now")

    near_four = await begin(owner, LIBRARY[5], ballpark=4.5, ballpark_to=5.0)

    assert near_four["b"]["tmdb_id"] == ids[1]


async def test_a_ballpark_guess_never_becomes_a_judgment(owner, db):
    await scale(owner)
    account = await account_id(owner)
    before = (await comparison_log(db, account), await dividers(db, account))
    await mark_watched(owner, LIBRARY[5], "now")

    step = await begin(owner, LIBRARY[5], ballpark=3.0)

    assert step["answered"] == 0, "a guess is not an answer"
    assert (await comparison_log(db, account), await dividers(db, account)) == before


# --- Early bail and graduation ---


async def test_bailing_after_the_band_locks_lands_provisionally_mid_band(owner, db):
    ids = await scale(owner, size=9, top=1, bottom=7)

    landed = await _bail_inside_the_band(owner, LIBRARY[9], ids)

    assert landed["rating"] == 3.5, "the stars were settled before the exact slot was"
    assert landed["provisional"] is True
    await assert_ordering_well_formed(db, await account_id(owner))
    await assert_bands_well_formed(db, await account_id(owner))


async def test_bailing_before_the_band_locks_is_refused(owner):
    await scale(owner)
    await mark_watched(owner, LIBRARY[5], "now")
    await begin(owner, LIBRARY[5])

    response = await owner.post(f"/api/placements/{LIBRARY[5].tmdb_id}/bail")

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "band_not_locked"


async def test_a_provisional_placement_graduates_once_its_answers_pin_it(owner, db):
    ids = await scale(owner, size=9, top=1, bottom=7)
    await _bail_inside_the_band(owner, LIBRARY[9], ids)
    assert _row_for(await rated(owner), LIBRARY[9].tmdb_id)["provisional"] is True

    # More answers, and the film's own judgments pin it exactly where it already sits -
    # which is the same bar a placement the owner answered through clears.
    step = await keep_comparing(owner, LIBRARY[9])
    while not step["done"]:
        opponent = step["b"]["tmdb_id"]
        await answer(owner, LIBRARY[9], opponent, "a" if ids.index(opponent) >= 4 else "b")
        step = await keep_comparing(owner, LIBRARY[9])

    assert _row_for(await rated(owner), LIBRARY[9].tmdb_id)["provisional"] is False
    await assert_ordering_well_formed(db, await account_id(owner))


async def test_a_provisional_graduates_on_comparisons_run_for_other_films(owner, db):
    """The double-duty opponent, and the owner is never asked about this film again.

    Every comparison another film's placement runs against a bailed film is evidence
    about that film too (onboarding-and-import.md), so placing its neighbours is enough
    to pin it - which is the mechanism the seed import leans on to work off its backlog
    of provisionals without a single extra question.
    """
    ids = await scale(owner, size=9, top=1, bottom=7)
    await _bail_inside_the_band(owner, LIBRARY[9], ids)
    assert _row_for(await rated(owner), LIBRARY[9].tmdb_id)["provisional"] is True

    # Two placements the owner ran for other films, each landing right beside it.
    for film, offset in ((LIBRARY[10], 0), (LIBRARY[11], 1)):
        order = [slot[0] for slot in ordering_of(await rated(owner))]
        await place_at(owner, film, order, order.index(LIBRARY[9].tmdb_id) + offset)

    assert _row_for(await rated(owner), LIBRARY[9].tmdb_id)["provisional"] is False
    await assert_ordering_well_formed(db, await account_id(owner))


async def test_a_placement_the_owner_answered_through_is_never_provisional(owner):
    ids = await scale(owner)

    await place_at(owner, LIBRARY[5], ids, 4)

    assert _row_for(await rated(owner), LIBRARY[5].tmdb_id)["provisional"] is False


# --- Keep comparing ---


async def test_keep_comparing_asks_the_band_edge_question_first(owner):
    await scale(owner, size=7, top=1, bottom=5)

    step = await keep_comparing(owner, LIBRARY[4])

    assert step["kind"] == "band"
    assert [option["band"] for option in step["options"]] == [3.5, 3.0]


async def test_a_band_edge_answer_moves_the_divider_and_the_film_changes_band(owner, db):
    await scale(owner, size=7, top=1, bottom=5)
    account = await account_id(owner)
    before = (await dividers(db, account), await ordering_snapshot(db, account))
    step = await keep_comparing(owner, LIBRARY[4])

    landed = await answer_band(owner, LIBRARY[4], 3.0, step["options"][1]["exemplar"]["tmdb_id"])

    assert landed["rating"] == 3.0
    assert (await dividers(db, account))[3.5] < before[0][3.5], "the boundary came up past it"
    assert await ordering_snapshot(db, account) == before[1], "a band answer moves no film"
    await assert_bands_well_formed(db, account)


async def test_the_band_edge_question_is_asked_on_both_sides_of_a_film(owner, db):
    await scale(owner, size=9, top=1, bottom=7)

    # Pressed against the divider below it: "is this really only a 3.5?"
    below = await keep_comparing(owner, LIBRARY[6])
    assert [option["band"] for option in below["options"]] == [3.5, 3.0]

    # And against the one above it: "is this really a whole 4.0?" - the same doubt,
    # pointing the other way, and a film at the top of its band has only this one.
    above = await keep_comparing(owner, LIBRARY[2])
    assert [option["band"] for option in above["options"]] == [4.0, 3.5]
    await assert_bands_well_formed(db, await account_id(owner))


async def test_a_band_edge_answer_can_move_a_film_up_a_band(owner, db):
    ids = await scale(owner, size=9, top=1, bottom=7)
    step = await keep_comparing(owner, LIBRARY[2])

    landed = await answer_band(owner, LIBRARY[2], 4.0, step["options"][0]["exemplar"]["tmdb_id"])

    assert landed["rating"] == 4.0
    assert bands_of(await rated(owner))[ids[1]] == 4.0, "the anchor is still where it was"
    await assert_bands_well_formed(db, await account_id(owner))
    await assert_bands_derived(db, await account_id(owner), bands_of(await rated(owner)))


async def test_an_anchor_carried_out_of_its_band_by_a_divider_is_retired(owner, db):
    """A tied anchor rides the slot across the divider, so its status goes, not its seat."""
    ids = await scale(owner, size=9, top=1, bottom=7)
    account = await account_id(owner)

    # Tie a film to the 3.0 anchor, then answer the band edge under both of them.
    await mark_watched(owner, LIBRARY[9], "now")
    step = await begin(owner, LIBRARY[9])
    while step["b"]["tmdb_id"] != ids[7]:
        step = await answer(owner, LIBRARY[9], step["b"]["tmdb_id"], "b")
    await answer(owner, LIBRARY[9], ids[7], "tied")

    edge = await keep_comparing(owner, LIBRARY[9])
    assert edge["kind"] == "band"
    await answer_band(
        owner, LIBRARY[9], edge["options"][0]["band"], edge["options"][0]["exemplar"]["tmdb_id"]
    )

    derived = bands_of(await rated(owner))
    assert derived[ids[7]] == 3.5, "the divider carried the whole slot across"
    assert derived[LIBRARY[9].tmdb_id] == 3.5
    assert 3.0 not in await anchor_rows(db, account), "so the 3.0 anchor is not one any more"
    assert [slot for slot in await ordering_snapshot(db, account) if len(slot) > 1], (
        "and both films kept the seat their tie earned"
    )
    await assert_bands_well_formed(db, account)


async def test_an_anchor_carried_out_of_its_band_by_its_own_answer_is_retired(owner, db):
    """The other way an anchor leaves its band: it moves, rather than the divider moving.

    Keep-comparing the 4.0 anchor against the film above it and saying it is the better
    one carries it past the divider over its band. The dividers stand where they stood -
    they still separate the same other films - so this is the film leaving its own band
    under its own answer, and the lifecycle's reply is re-placement's (rating-system.md,
    "The anchor lifecycle"): the status goes and the seat the answer earned stays.
    """
    ids = await scale(owner, size=7, top=1, bottom=5)
    account = await account_id(owner)

    step = await keep_comparing(owner, LIBRARY[1])
    assert step["kind"] == "comparison"
    while not step["done"]:
        landed = await answer(owner, LIBRARY[1], step["b"]["tmdb_id"], "a")
        step = await keep_comparing(owner, LIBRARY[1])

    assert landed["position"] == 1, "the film keeps the seat its own answers earned"
    assert await anchor_rows(db, account) == {3.0: ids[5]}, "and loses only the 4.0 status"
    assert 4.0 not in bands_of(await rated(owner)).values()
    await assert_ordering_well_formed(db, account)
    await assert_bands_well_formed(db, account)


async def test_keep_comparing_an_anchor_that_agrees_leaves_it_anchored(owner, db):
    """The other side of it: an answer the anchor's own position already implies claims nothing."""
    ids = await scale(owner, size=7, top=1, bottom=5)
    account = await account_id(owner)
    before = (await ordering_snapshot(db, account), await dividers(db, account))

    step = await keep_comparing(owner, LIBRARY[1])
    assert step["kind"] == "comparison"
    opponent = step["b"]["tmdb_id"]
    await answer(owner, LIBRARY[1], opponent, "b" if opponent == ids[0] else "a")

    assert (await ordering_snapshot(db, account), await dividers(db, account)) == before
    assert await anchor_rows(db, account) == {4.0: ids[1], 3.0: ids[5]}
    await assert_bands_well_formed(db, account)


async def test_a_keep_comparing_answer_that_agrees_moves_nothing(owner, db):
    ids = await scale(owner)
    account = await account_id(owner)
    before = (await ordering_snapshot(db, account), await dividers(db, account))

    step = await keep_comparing(owner, LIBRARY[2])
    assert step["kind"] == "comparison"
    await answer(
        owner, LIBRARY[2], step["b"]["tmdb_id"], "b" if step["b"]["tmdb_id"] == ids[1] else "a"
    )

    assert (await ordering_snapshot(db, account), await dividers(db, account)) == before


async def test_a_keep_comparing_answer_can_move_the_film_past_a_neighbour(owner, db):
    ids = await scale(owner, size=7, top=1, bottom=5)
    account = await account_id(owner)

    step = await keep_comparing(owner, LIBRARY[3])
    assert step["kind"] == "comparison"
    opponent = step["b"]["tmdb_id"]
    landed = await answer(owner, LIBRARY[3], opponent, "a")

    expected = list(ids)
    expected.remove(LIBRARY[3].tmdb_id)
    expected.insert(expected.index(opponent), LIBRARY[3].tmdb_id)
    assert [slot[0] for slot in ordering_of(await rated(owner))] == expected
    assert landed["done"] is True
    await assert_ordering_well_formed(db, account)
    await assert_bands_well_formed(db, account)


async def test_keep_comparing_ends_rather_than_asking_the_same_pair_twice(owner):
    ids = await scale(owner)

    step = await keep_comparing(owner, LIBRARY[2])
    seen = set()
    while not step["done"]:
        assert step["b"]["tmdb_id"] not in seen
        seen.add(step["b"]["tmdb_id"])
        await answer(owner, LIBRARY[2], step["b"]["tmdb_id"], "skip")
        step = await keep_comparing(owner, LIBRARY[2])

    assert seen <= {ids[1], ids[3]}, "only the immediate neighbours are ever offered"


async def test_keep_comparing_on_a_film_that_was_never_placed_is_refused(owner):
    await mark_watched(owner, LIBRARY[0], "later")

    response = await owner.post(f"/api/placements/{LIBRARY[0].tmdb_id}/keep-comparing")

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "not_placed"


# --- The designation mismatch ---


async def test_designating_a_film_outside_the_band_runs_a_re_placement(owner, db):
    ids = await scale(owner)

    result = await designate(owner, 4.0, LIBRARY[4])

    assert result["outcome"] == "re_placement"
    assert result["band"] == 4.0
    # The intent alone changes nothing: the old anchor stands until comparisons decide.
    assert (await anchor_rows(db, await account_id(owner)))[4.0] == ids[1]
    assert bands_of(await rated(owner))[ids[4]] is None


async def test_landing_in_the_band_completes_the_designation(owner, db):
    ids = await scale(owner)
    await designate(owner, 4.0, LIBRARY[4])

    landed = await replace_at(owner, LIBRARY[4], [i for i in ids if i != ids[4]], 1)

    assert landed["designated"] is True
    assert landed["rating"] == 4.0
    assert (await anchor_rows(db, await account_id(owner)))[4.0] == ids[4]
    await assert_bands_well_formed(db, await account_id(owner))


async def test_landing_outside_the_band_cancels_it_and_the_placement_stands(owner, db):
    ids = await scale(owner)
    account = await account_id(owner)
    await designate(owner, 3.0, LIBRARY[0])

    landed = await replace_at(owner, LIBRARY[0], [i for i in ids if i != ids[0]], 4)

    assert landed["designated"] is False
    assert landed["position"] == 5, "the comparisons decided, and their answer stands"
    assert (await anchor_rows(db, account))[3.0] == ids[3], "the band kept its own anchor"
    await assert_ordering_well_formed(db, account)
    await assert_bands_well_formed(db, account)


async def test_re_placing_an_anchor_out_of_its_band_retires_it(owner, db):
    ids = await scale(owner)
    account = await account_id(owner)

    # The owner reaches for a 5.0 that is nowhere near one, and the comparisons say so.
    await designate(owner, 5.0, LIBRARY[1])
    landed = await replace_at(owner, LIBRARY[1], [i for i in ids if i != ids[1]], 4)

    assert landed["designated"] is False
    assert 4.0 not in await anchor_rows(db, account), "a canonical 4.0 cannot live among the 3.0s"
    assert 5.0 not in await anchor_rows(db, account)
    await assert_bands_well_formed(db, account)


async def test_the_re_placement_resumes_where_the_owner_left_it(owner):
    ids = await scale(owner)
    await designate(owner, 4.0, LIBRARY[4])
    without = [i for i in ids if i != ids[4]]

    step = await begin(owner, LIBRARY[4])
    opponent = step["b"]["tmdb_id"]
    await answer(owner, LIBRARY[4], opponent, "a" if without.index(opponent) >= 1 else "b")
    resumed = await begin(owner, LIBRARY[4])

    assert resumed["done"] is False
    assert resumed["answered"] == 1, "the answer already given still counts"


# --- The Rated screen ---


async def test_the_rated_screen_groups_the_ordering_by_band_and_badges_the_anchors(owner, db):
    ids = await scale(owner)

    payload = await rated(owner)

    assert [group["band"] for group in payload["groups"]] == [None, 4.0, 3.5, 3.0, None]
    assert ordering_of(payload) == [[film_id] for film_id in ids]
    badged = {
        film["tmdb_id"]
        for group in payload["groups"]
        for slot in group["slots"]
        for film in slot
        if film["anchor"]
    }
    assert badged == {ids[1], ids[3]}
    assert payload["bands"] == [4.0, 3.5, 3.0], "the jump-to-band control's targets"
    await assert_bands_derived(db, await account_id(owner), bands_of(payload))


async def test_a_tie_group_stays_one_slot_inside_its_band(owner, db):
    await scale(owner)
    await mark_watched(owner, LIBRARY[5], "now")
    step = await begin(owner, LIBRARY[5])
    await answer(owner, LIBRARY[5], step["b"]["tmdb_id"], "tied")

    payload = await rated(owner)

    tied = [slot for slot in ordering_of(payload) if len(slot) > 1]
    assert len(tied) == 1 and LIBRARY[5].tmdb_id in tied[0]
    await assert_ordering_well_formed(db, await account_id(owner))


async def test_a_non_position_sort_drops_the_grouping_for_a_flat_list(owner):
    await scale(owner)

    payload = await rated(owner, sort="title")

    assert payload["groups"] is None
    assert [film["title"] for film in payload["films"]] == sorted(
        film["title"] for film in payload["films"]
    )


async def test_the_recently_rated_sort_puts_the_newest_placement_first(owner):
    ids = await scale(owner)

    payload = await rated(owner, sort="rated")

    assert payload["films"][0]["tmdb_id"] == ids[-1], "the last film placed is the most recent"


async def test_re_placing_a_film_is_what_recently_rated_means_by_recent(owner):
    """screens-and-flows.md: "recently rated (last placement *or re-placement*)"."""
    ids = await scale(owner)
    assert (await rated(owner, sort="rated"))["films"][0]["tmdb_id"] == ids[-1]

    # A designation the comparisons disagree with, which runs a re-placement either way.
    await designate(owner, 3.0, LIBRARY[0])
    await replace_at(owner, LIBRARY[0], [i for i in ids if i != ids[0]], 4)

    payload = await rated(owner, sort="rated")
    assert payload["films"][0]["tmdb_id"] == ids[0], "re-placing a film is rating it again"


async def test_the_band_range_filter_narrows_to_the_bands_asked_for(owner):
    ids = await scale(owner)

    payload = await rated(owner, band_min=3.5, band_max=4.0)

    assert ordering_of(payload) == [[ids[1]], [ids[2]]]
    assert payload["bands"] == [4.0, 3.5, 3.0], "the menu still offers what the whole screen has"


async def test_a_film_with_no_band_yet_is_outside_every_band_range(owner):
    ids = await scale(owner)

    payload = await rated(owner, band_min=0.5, band_max=5.0)

    assert ids[0] not in bands_of(payload)
    assert ids[4] not in bands_of(payload)


async def test_the_genre_and_decade_filters_never_empty_their_own_menus(owner):
    await build_ordering(owner, [LIBRARY[0]])
    await place(owner, WESTERN, "b")
    await place(owner, COMEDY, "b")

    payload = await rated(owner, genre="Western")

    assert [slot[0] for slot in ordering_of(payload)] == [WESTERN.tmdb_id]
    assert "Comedy" in payload["genres"] and "Western" in payload["genres"]
    assert 2000 in payload["decades"] and 1970 in payload["decades"]

    by_decade = await rated(owner, decade=1970)
    assert [slot[0] for slot in ordering_of(by_decade)] == [COMEDY.tmdb_id]


# --- The anchor-designation nudge ---


async def test_the_nudge_sits_atop_rated_until_the_first_anchor_exists(owner):
    await build_ordering(owner, LIBRARY[:3])

    assert (await rated(owner))["anchor_nudge"] is True
    assert (await anchors(owner))["nudge"] is True

    await designate(owner, 4.0, LIBRARY[1])

    assert (await rated(owner))["anchor_nudge"] is False
    assert (await anchors(owner))["nudge"] is False


async def test_the_nudge_shows_on_a_position_only_done_screen(owner):
    landed, _ = await place(owner, LIBRARY[0], "b")

    assert landed["rating"] is None
    assert landed["anchor_nudge"] is True


async def test_the_nudge_is_gone_from_the_done_screen_once_an_anchor_exists(owner):
    ids = await scale(owner)

    landed = await place_at(owner, LIBRARY[5], ids, 0)

    assert landed["rating"] is None, "still position-only up there"
    assert landed["anchor_nudge"] is False, "but the missing stars are no longer unexplained"


# --- Realms ---


async def test_one_account_never_sees_anothers_anchors_or_dividers(owner, other_owner, db):
    await scale(owner)
    await build_ordering(other_owner, LIBRARY[:2])

    assert (await anchors(other_owner))["nudge"] is True
    assert bands_of(await rated(other_owner)) == {film.tmdb_id: None for film in LIBRARY[:2]}
    assert await dividers(db, await account_id(other_owner)) == {}
    await assert_bands_well_formed(db, await account_id(owner))
    await assert_bands_well_formed(db, await account_id(other_owner))


async def _second_four(owner, ids):
    """Land a film in the 4.0 band that is not its anchor, through the sliver question."""
    step = await place_at(owner, LIBRARY[5], ids, 2)
    assert step["kind"] == "band"
    await answer_band(owner, LIBRARY[5], 4.0, step["options"][0]["exemplar"]["tmdb_id"])


async def _bail_inside_the_band(owner, film, ids):
    """Answer until the stars are settled but the exact slot is not, then stop there."""
    await mark_watched(owner, film, "now")
    step = await begin(owner, film)
    while not step["done"] and not step["band_locked"]:
        opponent = step["b"]["tmdb_id"]
        step = await answer(owner, film, opponent, "a" if ids.index(opponent) >= 4 else "b")
    assert step["done"] is False and step["band_locked"], "the search settled before it locked"
    return await bail(owner, film)


def _row_for(payload, tmdb_id):
    return next(
        film
        for group in payload["groups"]
        for slot in group["slots"]
        for film in slot
        if film["tmdb_id"] == tmdb_id
    )
