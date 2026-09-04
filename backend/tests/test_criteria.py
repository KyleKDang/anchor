"""The bonus question after a placement, and the quality list behind it.

These read as what the owner did: place a film, notice the card, answer it or walk past
it, turn it down or off on Profile. The card is a bonus, so most of what is asserted here
is what it costs when it is ignored - which is nothing, at every level: the placement, the
ordering, the ratings, and the next screen are all identical either way.

The one thing that is never asserted is which pair or quality the advisory selection
happened to choose beyond what the spec fixes: the pair comes from this flow and the
quality from this account's list, and inside that the tests pin only the rules the spec
states (taste-profile.md).
"""

import pytest

from anchor.models import BUILT_IN_QUALITIES
from flows import (
    LIBRARY,
    account_id,
    answer_criteria,
    ask_criteria,
    begin,
    build_ordering,
    place,
    profile,
    rated,
)
from invariants import (
    assert_appended_only,
    assert_bands_well_formed,
    assert_ordering_well_formed,
    comparison_log,
    criteria_log,
    dividers,
    ordering_snapshot,
    quality_list,
)

FIRST, SECOND, THIRD, FOURTH, FIFTH = LIBRARY[:5]


@pytest.fixture(autouse=True)
def stocked(tmdb):
    return tmdb.with_films(*LIBRARY)


async def card_from(client, film, verdict="b", **params):
    """Place a film and hand back the bonus card the done screen carried, if any."""
    landed, _ = await place(client, film, verdict, **params)
    return landed["criteria"]


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


async def test_a_placement_with_nothing_to_compare_against_offers_nothing(owner):
    """The first film answers no comparisons, so there is no pair to ask about."""
    assert await card_from(owner, FIRST) is None


async def test_a_placement_offers_the_card_on_the_done_screen(owner, db):
    await build_ordering(owner, [FIRST])

    card = await card_from(owner, SECOND)

    assert card is not None
    assert card["quality"] in BUILT_IN_QUALITIES
    assert await criteria_log(db, await account_id(owner)) == [
        (card["quality"], card["film_a"]["tmdb_id"], card["film_b"]["tmdb_id"], "skip")
    ]


async def test_the_card_asks_about_a_pair_the_owner_just_judged(owner):
    """ "Which had the better ___?" about films they compared a moment ago, or not at all."""
    await ask_criteria(owner, "often")
    await build_ordering(owner, [FIRST, SECOND])

    card = await card_from(owner, THIRD)

    assert card is not None
    pair = {card["film_a"]["tmdb_id"], card["film_b"]["tmdb_id"]}
    assert THIRD.tmdb_id in pair
    assert pair - {THIRD.tmdb_id} <= {FIRST.tmdb_id, SECOND.tmdb_id}


async def test_the_wording_is_a_template_and_the_card_carries_no_free_text(owner):
    """The system never invents a quality or a question: selection is the whole of it."""
    await build_ordering(owner, [FIRST])

    card = await card_from(owner, SECOND)

    assert card is not None
    assert set(card) == {"id", "quality", "film_a", "film_b"}
    assert card["quality"] in BUILT_IN_QUALITIES


async def test_a_placement_never_offers_a_second_card(owner, db):
    """Zero or one per placement: re-opening the done screen does not mint another."""
    await build_ordering(owner, [FIRST])
    card = await card_from(owner, SECOND)
    assert card is not None

    again = await begin(owner, SECOND)

    assert again["done"] is True
    assert again["criteria"] is None
    assert len(await criteria_log(db, await account_id(owner))) == 1


async def test_answering_never_triggers_another(owner, db):
    await build_ordering(owner, [FIRST])
    card = await card_from(owner, SECOND)
    assert card is not None

    await answer_criteria(owner, card, "a")

    assert await begin(owner, SECOND) == {**await begin(owner, SECOND), "criteria": None}
    assert len(await criteria_log(db, await account_id(owner))) == 1


# --- Answering, dismissing, and walking away ---


async def test_answering_records_the_verdict_on_the_offer(owner, db):
    await build_ordering(owner, [FIRST])
    card = await card_from(owner, SECOND)
    assert card is not None

    await answer_criteria(owner, card, "tied")

    assert await criteria_log(db, await account_id(owner)) == [
        (card["quality"], card["film_a"]["tmdb_id"], card["film_b"]["tmdb_id"], "tied")
    ]


async def test_ignoring_the_card_is_recorded_exactly_as_dismissing_it(owner, db):
    """There is no dismiss call to make: the offer already says the owner said nothing."""
    await build_ordering(owner, [FIRST])
    card = await card_from(owner, SECOND)
    assert card is not None

    # The owner walks off. Nothing is sent, and the record still exists to be counted.
    assert await criteria_log(db, await account_id(owner)) == [
        (card["quality"], card["film_a"]["tmdb_id"], card["film_b"]["tmdb_id"], "skip")
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


# --- The hard wall: answers are evidence, never an ordering (ADR 0007) ---


async def test_a_criteria_answer_never_moves_the_ordering(owner, db):
    await ask_criteria(owner, "often")
    await build_ordering(owner, [FIRST, SECOND])
    card = await card_from(owner, THIRD)
    assert card is not None
    account = await account_id(owner)
    before = await ordering_snapshot(db, account)
    ratings = await rated(owner)
    boundaries = await dividers(db, account)

    await answer_criteria(owner, card, "a")

    assert await ordering_snapshot(db, account) == before
    assert await dividers(db, account) == boundaries
    assert await rated(owner) == ratings
    await assert_ordering_well_formed(db, account)
    await assert_bands_well_formed(db, account)


async def test_an_answered_offer_only_ever_fills_in_its_own_verdict(owner, db):
    """The log is append-only bar this: the offer is one record, completed once."""
    await build_ordering(owner, [FIRST])
    card = await card_from(owner, SECOND)
    assert card is not None
    account = await account_id(owner)
    before = await comparison_log(db, account)

    await answer_criteria(owner, card, "b")

    assert_appended_only(before, await comparison_log(db, account), "answering a criteria card")


async def test_ignoring_the_card_leaves_the_placement_untouched(owner, db):
    """Answering and ignoring cost the same, so the account looks identical either way."""
    await ask_criteria(owner, "often")
    await build_ordering(owner, [FIRST, SECOND])
    account = await account_id(owner)

    card = await card_from(owner, THIRD)

    assert card is not None
    assert await ordering_snapshot(db, account) == [
        [FIRST.tmdb_id],
        [SECOND.tmdb_id],
        [THIRD.tmdb_id],
    ]


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


async def test_often_offers_after_every_placement_that_has_a_pair(owner, db):
    await ask_criteria(owner, "often")

    await build_ordering(owner, [FIRST, SECOND, THIRD, FOURTH])

    # The first film answered no comparison, so three placements could carry a card.
    assert len(await criteria_log(db, await account_id(owner))) == 3


async def test_a_manual_frequency_spaces_the_offers_out(owner, db):
    """Rarely means rarely: the first card lands, and then it goes quiet for a long while."""
    await ask_criteria(owner, "rarely")

    await build_ordering(owner, [FIRST, SECOND, THIRD, FOURTH, FIFTH])

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
    await build_ordering(owner, [FIRST, SECOND])
    assert len(await criteria_log(db, account)) == 1  # offered once, and ignored

    await build_ordering(owner, [THIRD, FOURTH, FIFTH])

    assert len(await criteria_log(db, account)) == 1


async def test_adaptive_keeps_asking_an_owner_who_keeps_answering(owner, db):
    """Engagement raises it: an owner who answers is asked again at the next placement."""
    account = await account_id(owner)
    await build_ordering(owner, [FIRST])
    first = await card_from(owner, SECOND)
    assert first is not None
    await answer_criteria(owner, first, "a")

    second = await card_from(owner, THIRD)

    assert second is not None
    assert len(await criteria_log(db, account)) == 2


async def test_the_rotation_works_through_the_quality_list(owner, db):
    """Until quality tags exist, the fallback is rotation - so the evidence spreads out."""
    await ask_criteria(owner, "often")

    await build_ordering(owner, [FIRST, SECOND, THIRD, FOURTH])

    asked = [entry[0] for entry in await criteria_log(db, await account_id(owner))]
    assert asked == list(BUILT_IN_QUALITIES[: len(asked)])
