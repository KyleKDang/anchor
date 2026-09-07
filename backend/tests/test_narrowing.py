"""Narrowing a range on the band picker: the questions, the seam, and where it lands.

These read as the owner's flow - select two or three bands you cannot choose between,
answer what you are asked, land - and what they pin is the rule each question follows,
never which film happened to satisfy it. Opponent choice is advisory (ADR 0001), so a
test that asserted "it asked about Film 04" would be pinning the wrong thing; a test that
asserts "it asked the weakest anchor of the upper band" pins the spec.

The library is deliberately flat: every fixture film carries the same vote statistics, so
the default order falls to the title tiebreak and a band's rank order is its films' order
in ``LIBRARY``. That makes "the weakest anchor" and "the bottom film" readable at a
glance rather than something a test has to look up.
"""

import pytest
from sqlalchemy import select

from anchor.models import AccountFilm, LifecycleState
from flows import (
    LIBRARY,
    account_id,
    answer,
    build_ordering,
    land,
    mark_anchor,
    mark_watched,
    narrow,
    ordering_of,
    picker,
    queue_of,
    rate,
    rated,
)
from invariants import (
    EXEMPLAR_COLUMNS,
    RANGE_COLUMNS,
    assert_appended_only,
    assert_ordering_well_formed,
    comparison_log,
)

SUBJECT = LIBRARY[11]
"""The film being rated in every flow here, kept out of every band the ranges cover."""


@pytest.fixture(autouse=True)
def stocked(tmdb):
    return tmdb.with_films(*LIBRARY)


async def a_seam(client):
    """Two neighbouring bands with anchors in both, and a watched film to rate against them.

    5.0 holds three films and marks its best and its worst; 4.5 holds three and marks its
    best and its worst. So the weakest 5.0 anchor and the strongest 4.5 anchor are both
    plainly identifiable, and both bands have a seam film to show at the boundary.
    """
    await build_ordering(client, LIBRARY[0:3], band=5.0)
    await build_ordering(client, LIBRARY[3:6], band=4.5)
    for film in (LIBRARY[0], LIBRARY[2], LIBRARY[3], LIBRARY[5]):
        await mark_anchor(client, film)
    await mark_watched(client, SUBJECT, "now")


# --- Three bands: the middle band's anchor, and either direction drops a band ---


async def test_three_bands_ask_the_middle_bands_anchor_nearest_the_middle(owner):
    """Either direction drops a band, which is what makes the middle band the question."""
    await build_ordering(owner, LIBRARY[0:3], band=5.0)
    await build_ordering(owner, LIBRARY[3:6], band=4.5)
    await build_ordering(owner, LIBRARY[6:9], band=4.0)
    for film in LIBRARY[3:6]:
        await mark_anchor(owner, film)
    await mark_watched(owner, SUBJECT, "now")

    step = await narrow(owner, SUBJECT, [5.0, 4.5, 4.0])

    assert step["question"]["band"] == 4.5, "the middle band is what a three-band range asks"
    assert step["question"]["anchor"] is True
    assert step["question"]["film"]["tmdb_id"] == LIBRARY[4].tmdb_id, "the middle of the pool"


async def test_better_than_the_middle_band_drops_the_bottom_band(owner):
    await build_ordering(owner, LIBRARY[0:3], band=5.0)
    await build_ordering(owner, LIBRARY[3:6], band=4.5)
    await build_ordering(owner, LIBRARY[6:9], band=4.0)
    await mark_anchor(owner, LIBRARY[4])
    await mark_watched(owner, SUBJECT, "now")

    step = await answer(owner, SUBJECT, [5.0, 4.5, 4.0], ["better"])

    assert step["bands"] == [5.0, 4.5], "better than a 4.5 means at least 4.5"


async def test_worse_than_the_middle_band_drops_the_top_band(owner):
    await build_ordering(owner, LIBRARY[0:3], band=5.0)
    await build_ordering(owner, LIBRARY[3:6], band=4.5)
    await build_ordering(owner, LIBRARY[6:9], band=4.0)
    await mark_anchor(owner, LIBRARY[4])
    await mark_watched(owner, SUBJECT, "now")

    step = await answer(owner, SUBJECT, [5.0, 4.5, 4.0], ["worse"])

    assert step["bands"] == [4.5, 4.0], "worse than a 4.5 means at most 4.5"


# --- Two bands: the weakest upper anchor, then the strongest lower ---


async def test_two_bands_ask_the_weakest_upper_anchor_first(owner):
    """Beating it settles the upper band, which is the question most likely to end it."""
    await a_seam(owner)

    step = await narrow(owner, SUBJECT, [5.0, 4.5])

    assert step["question"]["band"] == 5.0
    assert step["question"]["film"]["tmdb_id"] == LIBRARY[2].tmdb_id, "the pool's worst-ranked"


async def test_beating_the_upper_anchor_settles_the_upper_band(owner):
    await a_seam(owner)

    step = await answer(owner, SUBJECT, [5.0, 4.5], ["better"])

    assert step["band"] == 5.0
    assert step["question"] is None, "nothing is left to ask"


async def test_about_the_same_as_the_upper_anchor_settles_that_band(owner):
    """About the same means exactly that band, and it ends the search there."""
    await a_seam(owner)

    step = await answer(owner, SUBJECT, [5.0, 4.5], ["same"])

    assert step["band"] == 5.0


async def test_losing_to_the_upper_anchor_moves_on_to_the_strongest_lower_one(owner):
    await a_seam(owner)

    step = await answer(owner, SUBJECT, [5.0, 4.5], ["worse"])

    assert step["bands"] == [5.0, 4.5], "at most a 5.0 rules nothing out of this range"
    assert step["question"]["band"] == 4.5
    assert step["question"]["film"]["tmdb_id"] == LIBRARY[3].tmdb_id, "the pool's best-ranked"


async def test_losing_to_the_lower_anchor_settles_the_lower_band(owner):
    await a_seam(owner)

    step = await answer(owner, SUBJECT, [5.0, 4.5], ["worse", "worse"])

    assert step["band"] == 4.5


# --- The seam, where anchors cannot help ---


async def test_the_unhelpful_pair_of_answers_reaches_the_boundary_question(owner):
    """An anchor bounds and never floors, so the seam is settled by the seam films."""
    await a_seam(owner)

    step = await answer(owner, SUBJECT, [5.0, 4.5], ["worse", "better"])

    assert step["band"] is None, "anchors cannot settle it"
    assert step["boundary"]["upper"]["tmdb_id"] == LIBRARY[2].tmdb_id, "the 5.0 band's bottom"
    assert step["boundary"]["upper_band"] == 5.0
    assert step["boundary"]["lower"]["tmdb_id"] == LIBRARY[3].tmdb_id, "the 4.5 band's top"
    assert step["boundary"]["lower_band"] == 4.5


async def test_the_boundary_lands_the_film_beside_the_lower_film_it_is_closer_to(owner, db):
    await a_seam(owner)

    landed = await land(
        owner,
        SUBJECT,
        4.5,
        bands=[5.0, 4.5],
        answered=["worse", "better"],
        closer=LIBRARY[3].tmdb_id,
    )

    assert landed["band"] == 4.5
    assert landed["rank"] == 1, "beside the top film of the lower band"
    assert (ordering_of(await rated(owner)))[4.5][:2] == [SUBJECT.tmdb_id, LIBRARY[3].tmdb_id]
    await assert_ordering_well_formed(db, await account_id(owner))


async def test_a_film_that_loses_to_every_top_anchor_can_still_land_in_the_top_band(owner, db):
    """An anchor is a bound, never a floor: a low 5.0 stays exactly as possible."""
    await a_seam(owner)

    landed = await land(
        owner,
        SUBJECT,
        5.0,
        bands=[5.0, 4.5],
        answered=["worse", "better"],
        closer=LIBRARY[2].tmdb_id,
    )

    assert landed["band"] == 5.0, "losing to every 5.0 anchor did not make it a 4.5"
    assert landed["rank"] == 4, "beside the bottom film of the upper band"
    assert (ordering_of(await rated(owner)))[5.0][-1] == SUBJECT.tmdb_id
    await assert_ordering_well_formed(db, await account_id(owner))


async def test_a_film_that_is_not_a_boundary_film_is_refused(owner):
    await a_seam(owner)

    await land(
        owner,
        SUBJECT,
        5.0,
        bands=[5.0, 4.5],
        answered=["worse", "better"],
        closer=LIBRARY[0].tmdb_id,
        expect=409,
    )


# --- Stand-ins, and bands that cannot be asked about ---


async def test_a_band_with_no_anchor_is_stood_for_by_its_seam_film(owner):
    """The upper band's stand-in is the film nearest the seam: its bottom film."""
    await build_ordering(owner, LIBRARY[0:3], band=5.0)
    await build_ordering(owner, LIBRARY[3:6], band=4.5)
    await mark_watched(owner, SUBJECT, "now")

    step = await narrow(owner, SUBJECT, [5.0, 4.5])

    assert step["question"]["anchor"] is False
    assert step["question"]["film"]["tmdb_id"] == LIBRARY[2].tmdb_id


async def test_a_middle_band_with_no_anchor_is_stood_for_by_its_middle_film(owner):
    await build_ordering(owner, LIBRARY[0:2], band=5.0)
    await build_ordering(owner, LIBRARY[3:6], band=4.5)
    await build_ordering(owner, LIBRARY[6:8], band=4.0)
    await mark_watched(owner, SUBJECT, "now")

    step = await narrow(owner, SUBJECT, [5.0, 4.5, 4.0])

    assert step["question"]["band"] == 4.5
    assert step["question"]["anchor"] is False
    assert step["question"]["film"]["tmdb_id"] == LIBRARY[4].tmdb_id


async def test_a_range_whose_bands_hold_no_film_falls_to_the_owners_pick(owner, db):
    """A band with no film cannot be asked about, so what remains is the owner's to pick."""
    await mark_watched(owner, SUBJECT, "now")

    step = await narrow(owner, SUBJECT, [2.0, 1.5])

    assert step["choose"] is True
    assert step["bands"] == [2.0, 1.5]
    assert step["question"] is None and step["boundary"] is None

    landed = await land(owner, SUBJECT, 1.5, bands=[2.0, 1.5])

    assert landed["band"] == 1.5
    await assert_ordering_well_formed(db, await account_id(owner))


async def test_the_owners_last_resort_pick_must_be_a_band_the_answers_left(owner):
    await a_seam(owner)

    await land(owner, SUBJECT, 4.0, bands=[5.0, 4.5], answered=["better"], expect=409)


async def test_a_range_sitting_at_the_boundary_can_only_be_answered_by_it(owner):
    """The seam is what the boundary question settles, not one of several ways to."""
    await a_seam(owner)

    await land(owner, SUBJECT, 5.0, bands=[5.0, 4.5], answered=["worse", "better"], expect=409)


async def test_a_boundary_film_named_where_no_boundary_is_waiting_is_refused(owner):
    await a_seam(owner)

    await land(
        owner,
        SUBJECT,
        5.0,
        bands=[5.0, 4.5],
        answered=["better"],
        closer=LIBRARY[2].tmdb_id,
        expect=409,
    )


async def test_answers_the_range_could_never_have_produced_are_refused(owner):
    """Answering past the end of a run is a screen out of step, not a judgment."""
    await a_seam(owner)

    await narrow(owner, SUBJECT, [5.0, 4.5], ["better", "worse"], expect=409)
    await land(owner, SUBJECT, 5.0, bands=[5.0, 4.5], answered=["better", "worse"], expect=409)


async def test_a_one_band_range_is_not_a_range(owner):
    """One band is not being unsure; that is the outright pick, which names no range."""
    await a_seam(owner)

    await land(owner, SUBJECT, 5.0, bands=[5.0], expect=422)


# --- Skipping ---


async def test_a_skip_swaps_in_the_bands_next_candidate_and_narrows_nothing(owner):
    await a_seam(owner)

    step = await answer(owner, SUBJECT, [5.0, 4.5], ["skip"])

    assert step["bands"] == [5.0, 4.5], "a skip proves nothing about the band"
    assert step["question"]["band"] == 5.0, "still the upper band's question"
    assert step["question"]["film"]["tmdb_id"] == LIBRARY[0].tmdb_id, "the next anchor up"


async def test_skipping_every_anchor_falls_through_to_the_stand_in(owner):
    """A stand-in stands for a band with no anchor *left* to ask about (CONTEXT.md)."""
    await build_ordering(owner, LIBRARY[0:3], band=5.0)
    await build_ordering(owner, LIBRARY[3:6], band=4.5)
    for film in LIBRARY[0:2]:
        await mark_anchor(owner, film)
    await mark_watched(owner, SUBJECT, "now")

    step = await answer(owner, SUBJECT, [5.0, 4.5], ["skip", "skip"])

    assert step["question"]["band"] == 5.0, "the upper band still has a film to stand for it"
    assert step["question"]["anchor"] is False
    assert step["question"]["film"]["tmdb_id"] == LIBRARY[2].tmdb_id, "the film nearest the seam"


async def test_a_band_skipped_to_exhaustion_is_passed_over(owner):
    """Nothing left to ask of a band is the same as nothing to ask: the range moves on."""
    await a_seam(owner)

    # The 5.0 band's bottom film is one of its anchors, so skipping both anchors leaves
    # it with no stand-in of its own and the question falls to the lower band.
    step = await answer(owner, SUBJECT, [5.0, 4.5], ["skip", "skip"])

    assert step["question"]["band"] == 4.5
    assert step["bands"] == [5.0, 4.5], "skips proved nothing, so nothing was ruled out"


# --- The landing, clipped to the answers ---


async def test_a_film_that_beat_an_anchor_lands_above_it(owner, db):
    """A landing never contradicts an answer just given (rating-system.md)."""
    await build_ordering(owner, LIBRARY[0:4], band=4.0)
    await build_ordering(owner, LIBRARY[4:6], band=3.5)
    await mark_anchor(owner, LIBRARY[3])
    await mark_watched(owner, SUBJECT, "now")

    step = await answer(owner, SUBJECT, [4.0, 3.5], ["better"])
    assert step["band"] == 4.0
    landed = await land(owner, SUBJECT, 4.0, bands=[4.0, 3.5], answered=["better"])

    row = (ordering_of(await rated(owner)))[4.0]
    assert row.index(SUBJECT.tmdb_id) < row.index(LIBRARY[3].tmdb_id)
    assert landed["rank"] == 4, "the default order would have seated it below"
    await assert_ordering_well_formed(db, await account_id(owner))


async def test_a_film_that_lost_to_a_stand_in_lands_below_it(owner, db):
    """The other half of the clip, and the reason a stand-in is a real opponent.

    The subject is the best-sorting film in the library, so the default order would seat
    it at the top of the band. Losing to the film standing for that band is what moves it.
    """
    subject = LIBRARY[0]
    await build_ordering(owner, LIBRARY[4:7], band=4.0)
    await build_ordering(owner, LIBRARY[7:10], band=3.5)
    await mark_watched(owner, subject, "now")

    step = await answer(owner, subject, [4.0, 3.5], ["worse", "worse"])
    assert step["band"] == 3.5, "losing to the lower band's stand-in settles it there"
    landed = await land(owner, subject, 3.5, bands=[4.0, 3.5], answered=["worse", "worse"])

    row = (ordering_of(await rated(owner)))[3.5]
    assert row.index(subject.tmdb_id) > row.index(LIBRARY[7].tmdb_id)
    assert landed["rank"] == 2, "the default order would have seated it first"
    await assert_ordering_well_formed(db, await account_id(owner))


async def test_a_re_rate_into_its_own_band_moves_only_where_an_answer_says_so(owner, db):
    """ "Keeps its rank" is about a rating re-affirmed, not one the owner has just argued."""
    await build_ordering(owner, LIBRARY[0:4], band=4.0)
    await build_ordering(owner, LIBRARY[4:6], band=3.5)
    await mark_anchor(owner, LIBRARY[0])
    subject = LIBRARY[3]

    await picker(owner, subject)
    step = await answer(owner, subject, [4.0, 3.5], ["better"])
    assert step["band"] == 4.0
    await land(owner, subject, 4.0, bands=[4.0, 3.5], answered=["better"])

    row = (ordering_of(await rated(owner)))[4.0]
    assert row.index(subject.tmdb_id) < row.index(LIBRARY[0].tmdb_id), "it beat the best anchor"
    await assert_ordering_well_formed(db, await account_id(owner))


# --- What a narrowing leaves behind ---


async def test_abandoning_mid_range_leaves_the_answers_in_the_log(owner, db):
    """The film stays watched-unrated on the rate-later queue, and the judgments stand."""
    account = await account_id(owner)
    await a_seam(owner)
    before = await comparison_log(db, account)

    await answer(owner, SUBJECT, [5.0, 4.5], ["worse"])

    after = await comparison_log(db, account)
    assert_appended_only(before, after, "abandoning a range")
    assert [row[1] for row in after[len(before) :]] == ["band_comparison"]
    async with db.sessions() as session:
        account_film = await session.scalar(
            select(AccountFilm).where(AccountFilm.film_id == SUBJECT.tmdb_id)
        )
        assert account_film.state is LifecycleState.watched_unrated
    assert SUBJECT.tmdb_id in queue_of(await rated(owner))


async def test_the_next_attempt_starts_the_picker_fresh(owner):
    """Nothing is stored while a narrowing runs, so an abandoned one leaves no place to resume."""
    await a_seam(owner)
    await answer(owner, SUBJECT, [5.0, 4.5], ["worse", "better"])

    step = await narrow(owner, SUBJECT, [5.0, 4.5])

    assert step["question"]["film"]["tmdb_id"] == LIBRARY[2].tmdb_id, "the first question again"


async def test_a_comparison_records_the_range_it_was_narrowing(owner, db):
    account = await account_id(owner)
    await a_seam(owner)

    await answer(owner, SUBJECT, [5.0, 4.5, 4.0], ["worse", "skip"])

    first, second = (await comparison_log(db, account))[-2:]
    assert [row[1] for row in (first, second)] == ["band_comparison", "band_comparison"]
    assert first[3] == SUBJECT.tmdb_id, "film_a is always the film being rated"
    assert first[4] in {LIBRARY[3].tmdb_id, LIBRARY[5].tmdb_id}, "an anchor of the middle band"
    assert first[5] == "b", "worse than the opponent: the opponent won"
    assert tuple(first[column] for column in RANGE_COLUMNS) == (5.0, 4.0)
    assert tuple(second[column] for column in RANGE_COLUMNS) == (4.5, 4.0), "a band had gone"


async def test_a_boundary_pick_names_both_exemplars_and_the_range(owner, db):
    account = await account_id(owner)
    await a_seam(owner)

    await land(
        owner,
        SUBJECT,
        4.5,
        bands=[5.0, 4.5],
        answered=["worse", "better"],
        closer=LIBRARY[3].tmdb_id,
    )

    row = (await comparison_log(db, account))[-1]
    assert row[1] == "band_pick" and row[6] == 4.5
    assert tuple(row[column] for column in RANGE_COLUMNS) == (5.0, 4.5)
    assert tuple(row[column] for column in EXEMPLAR_COLUMNS) == (
        LIBRARY[2].tmdb_id,
        LIBRARY[3].tmdb_id,
    )


async def test_a_pick_the_comparisons_settled_names_the_one_band_they_left(owner, db):
    """The range on a pick is the range it was narrowing, which is how the kinds differ."""
    account = await account_id(owner)
    await a_seam(owner)

    await land(owner, SUBJECT, 5.0, bands=[5.0, 4.5], answered=["better"])

    row = (await comparison_log(db, account))[-1]
    assert row[1] == "band_pick" and row[6] == 5.0
    assert tuple(row[column] for column in RANGE_COLUMNS) == (5.0, 5.0)
    assert tuple(row[column] for column in EXEMPLAR_COLUMNS) == (None, None)


async def test_a_last_resort_pick_names_the_bands_it_was_picked_from(owner, db):
    """Nothing could be asked, so the range the owner chose from is still whole."""
    account = await account_id(owner)
    await mark_watched(owner, SUBJECT, "now")

    await land(owner, SUBJECT, 1.5, bands=[2.0, 1.5])

    row = (await comparison_log(db, account))[-1]
    assert row[1] == "band_pick" and row[6] == 1.5
    assert tuple(row[column] for column in RANGE_COLUMNS) == (2.0, 1.5), "more than one band left"
    assert tuple(row[column] for column in EXEMPLAR_COLUMNS) == (None, None)


async def test_an_outright_pick_names_no_range_and_no_exemplars(owner, db):
    """The picker's one-tap path narrowed nothing, and the log says so rather than guessing."""
    account = await account_id(owner)
    await rate(owner, LIBRARY[0], 4.0)

    row = (await comparison_log(db, account))[-1]
    assert row[1] == "band_pick"
    assert tuple(row[column] for column in RANGE_COLUMNS) == (None, None)
    assert tuple(row[column] for column in EXEMPLAR_COLUMNS) == (None, None)


# --- The rules the seed does not touch ---


async def test_a_range_must_be_two_or_three_adjacent_bands(owner):
    await mark_watched(owner, SUBJECT, "now")

    await narrow(owner, SUBJECT, [5.0, 4.0], expect=422)
    await narrow(owner, SUBJECT, [4.5, 5.0], expect=422)
    await narrow(owner, SUBJECT, [5.0], expect=422)
    await narrow(owner, SUBJECT, [5.0, 4.5, 4.0, 3.5], expect=422)


async def test_an_unwatched_film_cannot_be_narrowed(owner):
    await narrow(owner, SUBJECT, [5.0, 4.5], expect=409)


async def an_even_pool(client):
    """A middle band whose four anchors leave two equally central, and no rule between them."""
    await build_ordering(client, LIBRARY[0:2], band=5.0)
    await build_ordering(client, LIBRARY[3:7], band=4.5)
    await build_ordering(client, LIBRARY[8:10], band=4.0)
    for film in LIBRARY[3:7]:
        await mark_anchor(client, film)
    await mark_watched(client, SUBJECT, "now")


async def test_the_middle_anchor_rule_holds_whichever_way_the_seed_falls(owner):
    """The seed decides an exact tie; the rule is what the test pins.

    Two runs of the same library under two seeds. What is asserted is that the film asked
    about is one of the pool's two central anchors - never which of them a seed produced.
    """
    await an_even_pool(owner)

    step = await narrow(owner, SUBJECT, [5.0, 4.5, 4.0])

    assert step["question"]["film"]["tmdb_id"] in {LIBRARY[4].tmdb_id, LIBRARY[5].tmdb_id}


@pytest.mark.settings(picker_seed=1)
async def test_the_middle_anchor_rule_holds_under_another_seed(owner):
    await an_even_pool(owner)

    step = await narrow(owner, SUBJECT, [5.0, 4.5, 4.0])

    assert step["question"]["film"]["tmdb_id"] in {LIBRARY[4].tmdb_id, LIBRARY[5].tmdb_id}


async def test_an_answer_to_a_question_that_is_not_being_asked_is_refused(owner):
    """The transcript names answers, never opponents: a run with no question takes none."""
    await a_seam(owner)

    await narrow(owner, SUBJECT, [5.0, 4.5], ["better"], "worse", expect=409)
