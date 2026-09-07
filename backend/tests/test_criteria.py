"""The criteria questions: the run after a rating, the session from a film's page, and
the quality list behind both.

These read as what the owner did: rate a film, notice the card, answer it or walk past
it, open a session about a film and answer until they leave, turn the run down or off on
Profile. The cards are a bonus, so most of what is asserted here is what they cost when
they are ignored - which is nothing, at every level: the rating, the ordering, and the
next screen are all identical either way.

The one thing that is never asserted is which pair or quality the advisory selection
happened to choose beyond what the spec fixes: the opponent comes down the ladder
taste-profile.md names and the quality from this account's list, and inside that the
tests pin only the rules the spec states.
"""

import pytest

from anchor.models import BUILT_IN_QUALITIES
from faketmdb import FilmFixture
from flows import (
    LIBRARY,
    account_id,
    add_quality,
    answer_criteria,
    answer_every_card,
    ask_criteria,
    build_ordering,
    compared_in_picker,
    dismiss_criteria,
    film_page,
    given_tags,
    mark_anchor,
    mark_watched,
    open_session,
    opponent_of,
    pair_of,
    pick,
    picker,
    profile,
    rate,
    rated,
    re_rate,
)
from invariants import (
    assert_appended_only,
    assert_ordering_well_formed,
    comparison_log,
    criteria_log,
    ordering_snapshot,
    quality_list,
)

FIRST, SECOND, THIRD, FOURTH, FIFTH = LIBRARY[:5]

EXTRAS = tuple(
    FilmFixture(2000 + n, f"Extra {n:02d}", release_date=f"{2000 + n}-01-01") for n in range(2)
)
"""Two films beyond the dozen, so a rotation walk can outlast a quality list of thirteen."""


@pytest.fixture(autouse=True)
def stocked(tmdb):
    return tmdb.with_films(*LIBRARY)


async def card_from(client, film, band=3.0):
    """Rate a film and hand back the bonus card the done screen carried, if any."""
    return (await rate(client, film, band))["criteria"]


# --- The quality list ---


async def test_the_built_in_dozen_is_seeded_when_the_account_becomes_real(owner, db):
    """Criteria questions and the picker both read this list, so it is never empty."""
    assert await quality_list(db, await account_id(owner)) == list(BUILT_IN_QUALITIES)


async def test_an_unverified_account_has_no_quality_list_yet(client, register, db):
    """The list is the account's first rows, so it waits for the account to become real."""
    signup = await client.post(
        "/api/auth/signup", json={"email": "waiting@example.com", "password": "correct horse 9"}
    )
    assert signup.status_code == 201, signup.text

    assert await quality_list(db, signup.json()["id"]) == []


async def test_one_owners_quality_list_is_not_another_s(owner, other_owner, db):
    mine, theirs = await account_id(owner), await account_id(other_owner)

    assert mine != theirs
    assert (
        await quality_list(db, mine) == await quality_list(db, theirs) == list(BUILT_IN_QUALITIES)
    )


# --- Being offered the card ---


async def test_a_rating_with_nothing_to_set_it_against_offers_nothing(owner):
    """The first film has no opponent anywhere on the ladder, so there is no pair."""
    assert await card_from(owner, FIRST) is None


async def test_a_rating_offers_the_card_on_the_done_screen(owner, db):
    await build_ordering(owner, [FIRST], band=3.0)

    card = await card_from(owner, SECOND)

    assert card is not None
    assert card["quality"] in BUILT_IN_QUALITIES
    assert await criteria_log(db, await account_id(owner)) == [
        (card["quality"], card["film_a"]["tmdb_id"], card["film_b"]["tmdb_id"], "skip", "placement")
    ]


async def test_the_card_asks_about_the_film_just_rated_and_one_it_sits_beside(owner):
    """ "Which had the better ___?" about a film the owner is likely to remember beside it."""
    await ask_criteria(owner, "often")
    await build_ordering(owner, [FIRST, SECOND], band=3.0)

    card = await card_from(owner, THIRD)

    assert card is not None
    pair = {card["film_a"]["tmdb_id"], card["film_b"]["tmdb_id"]}
    assert THIRD.tmdb_id in pair
    assert pair - {THIRD.tmdb_id} <= {FIRST.tmdb_id, SECOND.tmdb_id}


async def test_the_opponent_is_drawn_from_the_band_s_anchors_first(owner):
    """The film the owner is most certain about is the one the question is answerable against."""
    await ask_criteria(owner, "often")
    await build_ordering(owner, [FIRST, SECOND], band=3.0)
    await mark_anchor(owner, SECOND)

    card = await card_from(owner, THIRD)

    assert card is not None
    assert {card["film_a"]["tmdb_id"], card["film_b"]["tmdb_id"]} == {
        THIRD.tmdb_id,
        SECOND.tmdb_id,
    }


async def test_the_wording_is_a_template_and_the_card_carries_no_free_text(owner):
    """The system never invents a quality or a question: selection is the whole of it."""
    await build_ordering(owner, [FIRST], band=3.0)

    card = await card_from(owner, SECOND)

    assert card is not None
    assert set(card) == {"id", "quality", "film_a", "film_b"}
    assert card["quality"] in BUILT_IN_QUALITIES


async def test_a_rating_offers_one_card_until_it_is_answered(owner, db):
    """The landing carries one card: the next is minted by an answer and by nothing else."""
    await build_ordering(owner, [FIRST], band=3.0)

    card = await card_from(owner, SECOND)

    assert card is not None
    assert len(await criteria_log(db, await account_id(owner))) == 1


# --- The run on the done screen ---


async def test_answering_a_card_slides_the_next_one_in(owner, db):
    """A run: each answer brings the next card, about the same film, in the same context."""
    await ask_criteria(owner, "often")
    await build_ordering(owner, [FIRST, SECOND], band=3.0)
    card = await card_from(owner, THIRD)
    assert card is not None

    following = await answer_criteria(owner, card, "a")

    assert following is not None
    assert following["id"] != card["id"]
    assert THIRD.tmdb_id in pair_of(following)
    log = await criteria_log(db, await account_id(owner))
    assert [entry[4] for entry in log[-2:]] == ["placement", "placement"]


async def test_a_run_never_asks_the_same_pair_and_quality_twice(owner):
    await ask_criteria(owner, "often")
    await build_ordering(owner, [FIRST, SECOND], band=3.0)
    card = await card_from(owner, THIRD)
    assert card is not None

    met = await answer_every_card(owner, card)

    asked = [(frozenset(pair_of(seen)), seen["quality"]) for seen in met]
    assert len(asked) == len(set(asked))
    assert len(asked) > 2, "the run should have outlasted a couple of cards"


async def test_a_run_ends_when_nothing_unasked_remains(owner):
    """One opponent and a dozen qualities: twelve cards, then the run is over."""
    await build_ordering(owner, [FIRST], band=3.0)
    card = await card_from(owner, SECOND)
    assert card is not None

    met = await answer_every_card(owner, card)

    assert len(met) == len(BUILT_IN_QUALITIES)
    assert [seen["quality"] for seen in met] == list(BUILT_IN_QUALITIES)
    assert {frozenset(pair_of(seen)) for seen in met} == {
        frozenset({FIRST.tmdb_id, SECOND.tmdb_id})
    }


async def test_leaving_a_run_after_an_answer_leaves_the_last_card_reading_skip(owner, db):
    """Leaving is the absence of an answer: no next card is minted, and the row stands."""
    await ask_criteria(owner, "often")
    await build_ordering(owner, [FIRST, SECOND], band=3.0)
    card = await card_from(owner, THIRD)
    assert card is not None
    following = await answer_criteria(owner, card, "a")
    assert following is not None

    # The owner taps dismiss on the second card, or simply leaves. Nothing is sent.
    log = await criteria_log(db, await account_id(owner))

    assert [entry[3] for entry in log[-2:]] == ["a", "skip"]


async def test_a_re_rate_s_run_is_recorded_as_a_re_rate(owner, db):
    await ask_criteria(owner, "often")
    await build_ordering(owner, [FIRST, SECOND, THIRD], band=3.0)
    account = await account_id(owner)
    before = len(await criteria_log(db, account))

    card = (await re_rate(owner, THIRD, 4.0))["criteria"]
    assert card is not None
    await answer_criteria(owner, card, "b")

    assert [entry[4] for entry in (await criteria_log(db, account))[before:]] == [
        "re_rate",
        "re_rate",
    ]


async def test_turning_the_run_off_ends_a_run_in_progress(owner):
    """Off is complete: an answer after the switch mints nothing more."""
    await ask_criteria(owner, "often")
    await build_ordering(owner, [FIRST, SECOND], band=3.0)
    card = await card_from(owner, THIRD)
    assert card is not None
    await ask_criteria(owner, "off")

    assert await answer_criteria(owner, card, "a") is None


# --- The session from a film's page ---


async def test_a_session_opens_from_a_rated_film_whatever_the_frequency_says(owner, db):
    """Pull-only and always available: the off switch governs the run alone."""
    await ask_criteria(owner, "off")
    await build_ordering(owner, [FIRST, SECOND], band=3.0)

    card = await open_session(owner, SECOND)

    assert card is not None
    assert SECOND.tmdb_id in pair_of(card)
    assert await criteria_log(db, await account_id(owner)) == [
        (
            card["quality"],
            card["film_a"]["tmdb_id"],
            card["film_b"]["tmdb_id"],
            "skip",
            "spontaneous",
        )
    ]


async def test_a_session_is_only_about_a_film_the_owner_has_rated(owner):
    await build_ordering(owner, [FIRST], band=3.0)
    await mark_watched(owner, SECOND, "later")

    refused = await open_session(owner, SECOND, expect=404)

    assert refused["error"]["code"] == "not_rated"


async def test_a_session_with_nothing_to_ask_opens_empty(owner):
    """A library of one has no opponent, so the session says so rather than inventing one."""
    await build_ordering(owner, [FIRST], band=3.0)

    assert await open_session(owner, FIRST) is None


async def test_a_session_serves_cards_until_nothing_unasked_remains(owner, db):
    await ask_criteria(owner, "off")
    await build_ordering(owner, [FIRST, SECOND], band=3.0)
    card = await open_session(owner, SECOND)
    assert card is not None

    met = await answer_every_card(owner, card, "ab")

    assert len(met) == len(BUILT_IN_QUALITIES)
    assert {frozenset(pair_of(seen)) for seen in met} == {
        frozenset({FIRST.tmdb_id, SECOND.tmdb_id})
    }
    assert {entry[4] for entry in await criteria_log(db, await account_id(owner))} == {
        "spontaneous"
    }


async def test_a_session_never_asks_the_same_pair_and_quality_twice(owner):
    await build_ordering(owner, [FIRST, SECOND, THIRD, FOURTH], band=3.0)
    card = await open_session(owner, FOURTH)
    assert card is not None

    met = await answer_every_card(owner, card)

    asked = [(frozenset(pair_of(seen)), seen["quality"]) for seen in met]
    assert len(asked) == len(set(asked))
    assert len(asked) == 3 * len(BUILT_IN_QUALITIES)


async def test_a_session_does_not_re_ask_what_the_run_already_asked(owner):
    """What the app has asked before is what it has asked, whichever home asked it."""
    await ask_criteria(owner, "often")
    await build_ordering(owner, [FIRST], band=3.0)
    run = await card_from(owner, SECOND)
    assert run is not None
    await answer_criteria(owner, run, "a")

    card = await open_session(owner, SECOND)

    assert card is not None
    assert card["quality"] != run["quality"]


async def test_leaving_a_session_leaves_its_last_offer_reading_skip(owner, db):
    await ask_criteria(owner, "off")
    await build_ordering(owner, [FIRST, SECOND], band=3.0)
    card = await open_session(owner, SECOND)
    assert card is not None
    following = await answer_criteria(owner, card, "tied")
    assert following is not None

    # The leave control. Nothing is sent.
    assert [entry[3] for entry in await criteria_log(db, await account_id(owner))] == [
        "tied",
        "skip",
    ]


async def test_dismissing_a_session_card_brings_the_next_without_an_answer(owner, db):
    """A question the owner cannot answer is waved away, not forced into "about the same"."""
    await ask_criteria(owner, "off")
    await build_ordering(owner, [FIRST, SECOND], band=3.0)
    card = await open_session(owner, SECOND)
    assert card is not None

    following = await dismiss_criteria(owner, card)

    assert following is not None
    assert following["id"] != card["id"]
    assert following["quality"] != card["quality"]
    assert [entry[3] for entry in await criteria_log(db, await account_id(owner))] == [
        "skip",
        "skip",
    ]


async def test_dismissing_a_run_card_ends_the_run(owner, db):
    """The run's dismiss is the same as leaving: nothing more comes, and nothing is written."""
    await build_ordering(owner, [FIRST], band=3.0)
    card = await card_from(owner, SECOND)
    assert card is not None

    assert await dismiss_criteria(owner, card) is None
    assert len(await criteria_log(db, await account_id(owner))) == 1


async def test_a_session_asks_about_varied_opponents(owner):
    """Each card sets the film against another opponent before any opponent is asked twice."""
    await build_ordering(owner, [FIRST, SECOND, THIRD, FOURTH], band=3.0)
    card = await open_session(owner, FOURTH)
    assert card is not None

    met = await answer_every_card(owner, card, limit=3)

    first_round = [opponent_of(seen, FOURTH) for seen in met]
    assert sorted(first_round) == sorted([FIRST.tmdb_id, SECOND.tmdb_id, THIRD.tmdb_id])


async def test_one_owner_cannot_open_a_session_on_another_s_film(owner, other_owner):
    await build_ordering(owner, [FIRST, SECOND], band=3.0)

    refused = await open_session(other_owner, SECOND, expect=404)

    assert refused["error"]["code"] == "not_rated"


# --- Where the opponent comes from (taste-profile.md) ---


async def test_the_ladder_runs_anchors_neighbours_picker_opponents_then_the_library(
    owner, db, tmdb
):
    """One film per rung, and a session walks them in the order the spec fixes.

    Nothing in selection is sampled, so the order is asserted outright: the anchor of the
    subject's band, then the film beside it on the wall, then the film the picker set it
    against, then a film from the rest of the library.
    """
    account = await account_id(owner)
    elsewhere = FilmFixture(3000, "Elsewhere", release_date="2010-01-01")
    tmdb.with_films(elsewhere)
    # The subject's band: the anchor at the top, the neighbour, then the subject. The
    # anchor is two places up, so the neighbour rung has a film of its own.
    await build_ordering(owner, [FIRST, SECOND, THIRD], band=3.0)
    await mark_anchor(owner, FIRST)
    await build_ordering(owner, [FOURTH], band=4.0)
    await build_ordering(owner, [elsewhere], band=2.0)
    await compared_in_picker(db, account, THIRD, FOURTH)

    card = await open_session(owner, THIRD)
    assert card is not None
    met = await answer_every_card(owner, card, limit=4)

    opponents = [opponent_of(seen, THIRD) for seen in met]
    assert opponents == [FIRST.tmdb_id, SECOND.tmdb_id, FOURTH.tmdb_id, elsewhere.tmdb_id]


# --- Answering, dismissing, and walking away ---


async def test_answering_records_the_verdict_on_the_offer(owner, db):
    await build_ordering(owner, [FIRST])
    card = await card_from(owner, SECOND)
    assert card is not None

    await answer_criteria(owner, card, "tied")

    assert (await criteria_log(db, await account_id(owner)))[0] == (
        card["quality"],
        card["film_a"]["tmdb_id"],
        card["film_b"]["tmdb_id"],
        "tied",
        "placement",
    )


async def test_ignoring_the_card_is_recorded_exactly_as_dismissing_it(owner, db):
    """There is no dismiss call to make: the offer already says the owner said nothing."""
    await build_ordering(owner, [FIRST])
    card = await card_from(owner, SECOND)
    assert card is not None

    # The owner walks off. Nothing is sent, and the record still exists to be counted.
    assert await criteria_log(db, await account_id(owner)) == [
        (card["quality"], card["film_a"]["tmdb_id"], card["film_b"]["tmdb_id"], "skip", "placement")
    ]


async def test_answering_the_same_card_twice_is_refused(owner):
    """The log keeps every judgment, so a second answer would have to erase the first."""
    await build_ordering(owner, [FIRST])
    card = await card_from(owner, SECOND)
    assert card is not None
    await answer_criteria(owner, card, "a")

    refused = await answer_criteria(owner, card, "b", expect=409)

    assert refused["error"]["code"] == "already_answered"


async def test_one_owner_cannot_answer_another_s_card(owner, other_owner):
    await build_ordering(owner, [FIRST])
    card = await card_from(owner, SECOND)
    assert card is not None

    refused = await answer_criteria(other_owner, card, "a", expect=404)

    assert refused["error"]["code"] == "no_such_offer"


async def test_the_card_takes_only_the_three_answers_it_offers(owner):
    """Skip is not one of them: not answering is the card being left alone."""
    await build_ordering(owner, [FIRST])
    card = await card_from(owner, SECOND)
    assert card is not None

    await answer_criteria(owner, card, "skip", expect=422)


async def test_the_film_page_lists_what_the_owner_answered_and_not_what_they_were_asked(
    owner,
):
    """An offer nobody answered is not something the owner said about the film."""
    await ask_criteria(owner, "off")
    await build_ordering(owner, [FIRST, SECOND], band=3.0)
    card = await open_session(owner, SECOND)
    assert card is not None
    following = await answer_criteria(owner, card, "a")
    assert following is not None  # and left on screen, unanswered

    said = (await film_page(owner, SECOND))["judgments"]

    assert [(one["kind"], one["verdict"]) for one in said] == [
        ("criteria", "a"),
        ("band_pick", None),
    ]
    assert said[0]["quality"] == card["quality"]


# --- The hard wall: answers are evidence, never an ordering (ADR 0007) ---


async def test_a_criteria_answer_never_moves_the_ordering(owner, db):
    await ask_criteria(owner, "often")
    await build_ordering(owner, [FIRST, SECOND], band=3.0)
    card = await card_from(owner, THIRD)
    assert card is not None
    account = await account_id(owner)
    before = await ordering_snapshot(db, account)
    wall = await rated(owner)

    await answer_criteria(owner, card, "a")

    assert await ordering_snapshot(db, account) == before
    assert await rated(owner) == wall
    await assert_ordering_well_formed(db, account)


async def test_a_session_answer_never_moves_the_ordering(owner, db):
    await build_ordering(owner, [FIRST, SECOND, THIRD], band=3.0)
    card = await open_session(owner, THIRD)
    assert card is not None
    account = await account_id(owner)
    before = await ordering_snapshot(db, account)
    wall = await rated(owner)

    await answer_every_card(owner, card, "ab")

    assert await ordering_snapshot(db, account) == before
    assert await rated(owner) == wall
    await assert_ordering_well_formed(db, account)


async def test_an_answered_offer_only_ever_fills_in_its_own_verdict(owner, db):
    """The log is append-only bar this: the offer is one record, completed once."""
    await build_ordering(owner, [FIRST], band=3.0)
    card = await card_from(owner, SECOND)
    assert card is not None
    account = await account_id(owner)
    before = await comparison_log(db, account)

    await answer_criteria(owner, card, "b")

    assert_appended_only(before, await comparison_log(db, account), "answering a criteria card")


async def test_ignoring_the_card_leaves_the_rating_untouched(owner, db):
    """Answering and ignoring cost the same, so the account looks identical either way."""
    await ask_criteria(owner, "often")
    await build_ordering(owner, [FIRST, SECOND], band=3.0)
    account = await account_id(owner)

    card = await card_from(owner, THIRD)

    assert card is not None
    assert sorted((await ordering_snapshot(db, account))[3.0]) == sorted(
        [FIRST.tmdb_id, SECOND.tmdb_id, THIRD.tmdb_id]
    )


# --- How often it asks ---


async def test_the_off_switch_stops_the_offers_and_records_nothing(owner, db):
    await ask_criteria(owner, "off")

    await build_ordering(owner, [FIRST, SECOND, THIRD])

    assert await criteria_log(db, await account_id(owner)) == []


async def test_turning_it_off_leaves_no_backlog_to_come_back_to(owner, db):
    """Off is complete, so switching back on asks about the next placement, not old ones."""
    await ask_criteria(owner, "off")
    await build_ordering(owner, [FIRST, SECOND, THIRD])
    await ask_criteria(owner, "often")

    card = await card_from(owner, FOURTH)

    assert card is not None
    assert len(await criteria_log(db, await account_id(owner))) == 1


async def test_often_offers_after_every_rating_that_has_a_pair(owner, db):
    await ask_criteria(owner, "often")

    await build_ordering(owner, [FIRST, SECOND, THIRD, FOURTH], band=3.0)

    # The first film had nobody to be set against, so three ratings could carry a card.
    assert len(await criteria_log(db, await account_id(owner))) == 3


async def test_a_manual_frequency_spaces_the_offers_out(owner, db):
    """Rarely means rarely: the first card lands, and then it goes quiet for a long while."""
    await ask_criteria(owner, "rarely")

    await build_ordering(owner, [FIRST, SECOND, THIRD, FOURTH, FIFTH], band=3.0)

    assert len(await criteria_log(db, await account_id(owner))) == 1


async def test_the_frequency_the_owner_chose_is_what_profile_shows(owner):
    assert (await profile(owner))["criteria_frequency"] == "adaptive"

    await ask_criteria(owner, "sometimes")

    assert (await profile(owner))["criteria_frequency"] == "sometimes"


async def test_a_frequency_anchor_does_not_offer_is_refused(owner):
    response = await owner.put("/api/profile/criteria", json={"frequency": "constantly"})

    assert response.status_code == 422, response.text


async def test_adaptive_backs_off_when_the_owner_keeps_walking_past_the_card(owner, db):
    """Non-engagement lowers the frequency, which is the whole point of recording ignores."""
    account = await account_id(owner)
    await build_ordering(owner, [FIRST, SECOND], band=3.0)
    assert len(await criteria_log(db, account)) == 1  # offered once, and ignored

    await build_ordering(owner, [THIRD, FOURTH, FIFTH], band=3.0)

    assert len(await criteria_log(db, account)) == 1


async def test_adaptive_keeps_asking_an_owner_who_keeps_answering(owner, db):
    """Engagement raises it: an owner who answers is asked again at the next rating."""
    account = await account_id(owner)
    await build_ordering(owner, [FIRST], band=3.0)
    first = await card_from(owner, SECOND)
    assert first is not None
    await answer_criteria(owner, first, "a")

    second = await card_from(owner, THIRD)

    assert second is not None
    # The first run: its answered card and the one the answer slid in. Then the new one.
    assert len(await criteria_log(db, account)) == 3


async def test_a_run_s_ignored_last_card_does_not_outweigh_the_answers_before_it(owner, db):
    """Every run ends on an unanswered card, so answering three then leaving is engagement."""
    account = await account_id(owner)
    await build_ordering(owner, [FIRST], band=3.0)
    card = await card_from(owner, SECOND)
    assert card is not None
    for verdict in "aab":
        card = await answer_criteria(owner, card, verdict)
        assert card is not None
    # Four offers made, three answered, the fourth left on screen.
    assert len(await criteria_log(db, account)) == 4

    assert await card_from(owner, THIRD) is not None


async def test_a_session_s_answers_raise_the_adaptive_frequency(owner, db):
    """Non-engagement lowered it; a session of answers is engagement, and brings it back."""
    account = await account_id(owner)
    await build_ordering(owner, [FIRST, SECOND], band=3.0)
    assert len(await criteria_log(db, account)) == 1  # offered once, and ignored
    await build_ordering(owner, [THIRD], band=3.0)
    assert len(await criteria_log(db, account)) == 1  # and now backed off

    card = await open_session(owner, THIRD)
    assert card is not None
    for verdict in "abab":
        card = await answer_criteria(owner, card, verdict)
        assert card is not None

    assert await card_from(owner, FOURTH) is not None


async def test_a_session_between_ratings_does_not_restart_the_gap(owner, db):
    """A manual gap is counted in ratings since the last *run* offer; a session resets nothing.

    The session sits between the two ratings the gap is counted over, so a dial that
    mistook its offer for one of its own would start counting again from it and find
    nothing had passed.
    """
    account = await account_id(owner)
    await ask_criteria(owner, "sometimes")
    await build_ordering(owner, [FIRST, SECOND], band=3.0)
    assert len(await criteria_log(db, account)) == 1
    # "Sometimes" waits one rating between offers, so this rating gets no card...
    assert await card_from(owner, THIRD) is None
    card = await open_session(owner, THIRD)
    assert card is not None
    await answer_criteria(owner, card, "a")

    # ...and the next does, exactly as if the session had never happened.
    assert await card_from(owner, FOURTH) is not None


async def test_a_session_before_any_run_does_not_spend_the_first_offer(owner, db):
    """The first rating that can carry a card gets one whatever the setting - still."""
    account = await account_id(owner)
    await ask_criteria(owner, "rarely")
    await build_ordering(owner, [FIRST], band=3.0)
    await ask_criteria(owner, "off")
    await build_ordering(owner, [SECOND], band=3.0)
    card = await open_session(owner, SECOND)
    assert card is not None
    await answer_criteria(owner, card, "a")
    assert all(entry[4] == "spontaneous" for entry in await criteria_log(db, account))
    await ask_criteria(owner, "rarely")

    assert await card_from(owner, THIRD) is not None


async def test_the_rotation_works_through_the_quality_list(owner, db):
    """Until quality tags exist, the fallback is rotation - so the evidence spreads out."""
    await ask_criteria(owner, "often")

    await build_ordering(owner, [FIRST, SECOND, THIRD, FOURTH], band=3.0)

    asked = [entry[0] for entry in await criteria_log(db, await account_id(owner))]
    assert asked == list(BUILT_IN_QUALITIES[: len(asked)])


# --- Preferring a pair that shares a quality tag ---


async def tagged_then_rated(client, db, subject, partner, shared="Tension", band=3.0):
    """Arrange the tags around a film the owner is about to rate, then rate it.

    Watched before tagged, because a tag is a row against the shared catalog: a film
    nobody has touched has no catalog row for it to hang off, and tagging one quietly
    does nothing. Marking it watched is what puts it there, and it is the step the owner
    takes before rating anyway.

    Hands back the bonus card the done screen carried, if any.
    """
    await mark_watched(client, subject, "now")
    await tag_everything_apart(db, subject.tmdb_id, partner.tmdb_id, shared=shared)
    await picker(client, subject)
    return (await pick(client, subject, band))["criteria"]


async def tag_everything_apart(db, subject, partner, shared="Tension"):
    """Give the two named films one tag in common, and every other film its own.

    Distinct tags everywhere else, so exactly one pair the card could name overlaps and a
    card naming any other pair is the preference failing rather than the arrangement
    being ambiguous. Both films are named by tmdb id, the way the card reports them.
    """
    await given_tags(db, subject, shared)
    await given_tags(db, partner, shared)
    spare = [name for name in BUILT_IN_QUALITIES if name != shared]
    others = [film for film in LIBRARY if film.tmdb_id not in {subject, partner}]
    for own, film in zip(spare, others, strict=False):
        await given_tags(db, film.tmdb_id, own)


async def test_the_card_prefers_the_pair_whose_films_share_a_quality_tag(owner, db):
    """Two films known for the same thing make the question about a real difference.

    The shared partner here is deliberately *not* the top of the ladder - FIRST is
    anchored, so it is the film selection would otherwise have reached for - so what is
    asserted is the preference itself rather than an accident of the ordering.
    """
    await ask_criteria(owner, "often")
    await build_ordering(owner, [FIRST, SECOND, THIRD, FOURTH], band=3.0)
    await mark_anchor(owner, FIRST)

    card = await tagged_then_rated(owner, db, FIFTH, SECOND)

    assert card is not None
    assert {card["film_a"]["tmdb_id"], card["film_b"]["tmdb_id"]} == {
        FIFTH.tmdb_id,
        SECOND.tmdb_id,
    }


async def test_the_card_asks_about_the_quality_the_pair_shares(owner, db):
    """The tag is not just how the pair is chosen; it is what the question is about."""
    await ask_criteria(owner, "often")
    await build_ordering(owner, [FIRST, SECOND, THIRD, FOURTH], band=3.0)
    await mark_anchor(owner, FIRST)

    card = await tagged_then_rated(owner, db, FIFTH, SECOND, shared="Ending")

    assert card is not None
    assert card["quality"] == "Ending"


async def test_a_library_with_nothing_in_common_falls_back_to_the_ladder(owner, db):
    """No overlap anywhere is the ordinary case, not the error case: the ladder decides."""
    await ask_criteria(owner, "often")
    await build_ordering(owner, [FIRST, SECOND, THIRD, FOURTH], band=3.0)
    await mark_anchor(owner, FIRST)
    await mark_watched(owner, FIFTH, "now")
    for own, film in zip(BUILT_IN_QUALITIES, LIBRARY, strict=False):
        await given_tags(db, film.tmdb_id, own)

    await picker(owner, FIFTH)
    card = (await pick(owner, FIFTH, 3.0))["criteria"]

    assert card is not None
    assert {card["film_a"]["tmdb_id"], card["film_b"]["tmdb_id"]} == {
        FIFTH.tmdb_id,
        FIRST.tmdb_id,
    }


async def test_a_quality_the_owner_invented_is_never_what_a_shared_tag_asks_about(owner, db):
    """A custom quality cannot be a tag, so the preference can never reach for one.

    Its only route to a card is the rotation, which the walk below takes to the end of
    the list to show.
    """
    await add_quality(owner, "Costumes")
    await ask_criteria(owner, "often")
    await build_ordering(owner, [FIRST, SECOND, THIRD, FOURTH], band=3.0)
    await mark_anchor(owner, FIRST)

    card = await tagged_then_rated(owner, db, FIFTH, SECOND)

    assert card is not None
    assert card["quality"] == "Tension"


async def test_a_tag_driven_question_does_not_cost_the_rotation_a_turn(owner, db):
    """The two routes to a quality do not share a cursor, so the walk keeps no holes.

    A rotation counting offers rather than the questions it chose would be spent by the
    tag-driven ones: every shared-tag card would skip a list entry without asking it, and
    the walk would lose exactly the entries at the end of the list - which is where an
    owner's own custom qualities sit, and the rotation is their only route to a card.
    """
    account = await account_id(owner)
    await ask_criteria(owner, "often")
    await build_ordering(owner, [FIRST, SECOND], band=3.0)
    # A tagged pair, so the next card is chosen by its tag rather than by the rotation.
    await tagged_then_rated(owner, db, THIRD, SECOND, shared="Ending")
    asked = [entry[0] for entry in await criteria_log(db, account)]
    assert asked[-1] == "Ending", "this test needs the second card to have come from a tag"

    await build_ordering(owner, [FOURTH, FIFTH], band=3.0)

    walked = [entry[0] for entry in await criteria_log(db, account)]
    assert "Ending" not in walked[2:], "the rotation asked a quality the tag had just used"
    assert walked[2:] == [name for name in BUILT_IN_QUALITIES if name not in walked[:2]][:2]


async def test_the_rotation_reaches_a_quality_the_owner_added(owner, db, tmdb):
    """The one route a custom quality has to a card, walked from one end to the other.

    Thirteen offers rather than a handful, because the whole claim is about the *last*
    entry on the list: a custom addition sits after the built-in dozen, so a walk that
    stops early would prove the rotation works and say nothing about whether an owner's
    own quality is ever asked at all.
    """
    tmdb.with_films(*EXTRAS)
    account = await account_id(owner)
    await add_quality(owner, "Costumes")
    await ask_criteria(owner, "often")

    await build_ordering(owner, [*LIBRARY, *EXTRAS], band=3.0)

    asked = [entry[0] for entry in await criteria_log(db, account)]
    assert asked == [*BUILT_IN_QUALITIES, "Costumes"]
