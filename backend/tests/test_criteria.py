"""The bonus question after a rating, and the quality list behind it.

These read as what the owner did: rate a film, notice the card, answer it or walk past
it, turn it down or off on Profile. The card is a bonus, so most of what is asserted here
is what it costs when it is ignored - which is nothing, at every level: the rating, the
ordering, and the next screen are all identical either way.

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
    ask_criteria,
    build_ordering,
    given_tags,
    mark_anchor,
    profile,
    rate,
    rated,
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
        (card["quality"], card["film_a"]["tmdb_id"], card["film_b"]["tmdb_id"], "skip")
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


async def test_a_rating_offers_at_most_one_card(owner, db):
    """Zero or one per rating: there is exactly one call that can mint one."""
    await build_ordering(owner, [FIRST], band=3.0)

    card = await card_from(owner, SECOND)

    assert card is not None
    assert len(await criteria_log(db, await account_id(owner))) == 1


async def test_answering_never_triggers_another(owner, db):
    await build_ordering(owner, [FIRST], band=3.0)
    card = await card_from(owner, SECOND)
    assert card is not None

    await answer_criteria(owner, card, "a")

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
    assert len(await criteria_log(db, account)) == 2


async def test_the_rotation_works_through_the_quality_list(owner, db):
    """Until quality tags exist, the fallback is rotation - so the evidence spreads out."""
    await ask_criteria(owner, "often")

    await build_ordering(owner, [FIRST, SECOND, THIRD, FOURTH], band=3.0)

    asked = [entry[0] for entry in await criteria_log(db, await account_id(owner))]
    assert asked == list(BUILT_IN_QUALITIES[: len(asked)])


# --- Preferring a pair that shares a quality tag ---


async def tag_everything_apart(db, subject, partner, shared="Tension"):
    """Give the two named films one tag in common, and every other film its own.

    Distinct tags everywhere else, so exactly one pair in the whole library overlaps and
    a card naming any other pair is the preference failing rather than the arrangement
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
    await tag_everything_apart(db, FIFTH.tmdb_id, SECOND.tmdb_id)

    card = await card_from(owner, FIFTH)

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
    await tag_everything_apart(db, FIFTH.tmdb_id, SECOND.tmdb_id, shared="Ending")

    card = await card_from(owner, FIFTH)

    assert card is not None
    assert card["quality"] == "Ending"


async def test_a_library_with_nothing_in_common_falls_back_to_the_ladder(owner, db):
    """No overlap anywhere is the ordinary case, not the error case: the ladder decides."""
    await ask_criteria(owner, "often")
    await build_ordering(owner, [FIRST, SECOND, THIRD, FOURTH], band=3.0)
    await mark_anchor(owner, FIRST)
    for own, film in zip(BUILT_IN_QUALITIES, LIBRARY, strict=False):
        await given_tags(db, film.tmdb_id, own)

    card = await card_from(owner, FIFTH)

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
    await tag_everything_apart(db, FIFTH.tmdb_id, SECOND.tmdb_id)

    card = await card_from(owner, FIFTH)

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
    await tag_everything_apart(db, THIRD.tmdb_id, SECOND.tmdb_id, shared="Ending")
    await rate(owner, THIRD, 3.0)
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
