"""The quality picker and the corrections that outlive a rewrite.

Two ways an owner says something about themselves outright, and one rule underneath
both: what they say is stored structurally, so a regeneration cannot clobber it.

The picker is the near-zero-effort half. Anchor guesses from what the owner has already
judged, ticks its guesses, and the owner's whole job is to untick what is wrong - so the
tests here are mostly about the guess being a guess: it is never a constraint until the
owner confirms it, and it stops being made the moment they do.

Corrections are the other half, and their test is the negative one. Thumbing down a claim
has to survive a rewrite that would make the same claim again, which is exactly what an
edit to the prose text could not do.
"""

import uuid

import pytest

from anchor import llm
from anchor.models import BUILT_IN_QUALITIES
from faketmdb import FilmFixture
from flows import (
    LIBRARY,
    account_id,
    add_quality,
    answer_criteria,
    ask_criteria,
    build_ordering,
    corrections,
    lift_correction,
    pick_qualities,
    place,
    profile,
    qualities,
    scale,
    thumb_down,
)
from invariants import criteria_log, profile_constraints, prose_versions

SMALL = pytest.mark.settings(
    readiness_forming_films=3,
    readiness_forming_bands=1,
    prose_placements_trigger=2,
    prose_drift_trigger=1,
    prose_staleness_comparisons=8,
)
"""The same small bars the prose tests run at: this is the same engine, spending less."""

BUILT_IN = len(BUILT_IN_QUALITIES)
"""The closed vocabulary the account's list is seeded with (taste-profile.md)."""

ROTATION = tuple(
    FilmFixture(2000 + n, f"Rotation {n:02d}", release_date=f"{1970 + n}-01-01")
    for n in range(BUILT_IN + 2)
)
"""Enough placements to offer a card for every built-in quality and then one more."""


@pytest.fixture(autouse=True)
def stocked(tmdb):
    return tmdb.with_films(*LIBRARY, *ROTATION)


async def settled(client, run_jobs, size=5):
    """An account with an ordering, its anchors set, and its first prose written."""
    await scale(client, size=size)
    await run_jobs()
    return uuid.UUID(await account_id(client))


def ticked(payload):
    return [entry["name"] for entry in payload["qualities"] if entry["checked"]]


def guessed(payload):
    return [entry["name"] for entry in payload["qualities"] if entry["suggested"]]


# --- Offered from the start, and never a gate ---


async def test_the_picker_is_offered_before_anything_has_been_earned(owner):
    """A cold account has a list to confirm; it just has nothing guessed for it yet.

    The list is seeded at account creation, so the picker is never a screen that has to
    explain why it is empty - the owner can state a preference on day one, and Anchor
    catching up with a guess later changes nothing they already said.
    """
    shown = await qualities(owner)

    assert len(shown["qualities"]) == BUILT_IN
    assert shown["answered"] is False
    assert ticked(shown) == []


async def test_answering_with_nothing_ticked_is_an_answer(owner, db):
    """Skippable means the empty answer is real, not a screen the owner cannot leave."""
    await pick_qualities(owner, [])

    shown = await qualities(owner)
    assert shown["answered"] is True
    assert ticked(shown) == []
    assert await profile_constraints(db, uuid.UUID(await account_id(owner))) == []


async def test_the_picker_is_editable_any_time(owner, db):
    """A multi-select is answered by what is left ticked, so unticking is an edit."""
    await pick_qualities(owner, ["Pacing", "Ending"])
    await pick_qualities(owner, ["Pacing", "Humor"])

    assert ticked(await qualities(owner)) == ["Pacing", "Humor"]
    stated = await profile_constraints(db, uuid.UUID(await account_id(owner)))
    # By name rather than in row order: two selections made in one answer share a
    # timestamp, and which of them the table hands back first is not a fact about Anchor.
    assert {name: live for _, name, _, live in stated} == {
        "Pacing": True,
        "Ending": False,
        "Humor": True,
    }


async def test_unticking_lifts_the_constraint_rather_than_deleting_it(owner, db):
    """The owner changing their mind is itself a fact about their taste."""
    await pick_qualities(owner, ["Tension"])
    await pick_qualities(owner, [])

    stated = await profile_constraints(db, uuid.UUID(await account_id(owner)))
    assert [(name, live) for _, name, _, live in stated] == [("Tension", False)]


async def test_a_quality_can_be_picked_again_after_being_let_go(owner, db):
    """Lifted rows stay lifted, so re-stating a selection is a fresh row saying so.

    Worth pinning because the one-live-pick-per-quality rule is enforced by the database,
    and an owner who unticks something in March and ticks it again in June is exactly the
    case where re-using the old row would have looked like the tidier answer.
    """
    await pick_qualities(owner, ["Tension"])
    await pick_qualities(owner, [])
    await pick_qualities(owner, ["Tension"])

    assert ticked(await qualities(owner)) == ["Tension"]
    stated = await profile_constraints(db, uuid.UUID(await account_id(owner)))
    assert [(name, live) for _, name, _, live in stated] == [("Tension", False), ("Tension", True)]


async def test_a_selection_is_stored_as_a_structural_constraint(owner, db):
    """Not prose the owner wrote, and not a flag on the quality: a constraint row."""
    await pick_qualities(owner, ["Score"])

    (constraint,) = await profile_constraints(db, uuid.UUID(await account_id(owner)))
    kind, name, content, live = constraint
    assert (kind, name, content, live) == ("quality_pick", "Score", None, True)


async def test_one_owners_picker_says_nothing_about_anothers(owner, other_owner, db):
    await pick_qualities(owner, ["Pacing"])

    assert ticked(await qualities(other_owner)) == []
    assert await profile_constraints(db, uuid.UUID(await account_id(other_owner))) == []


async def test_a_quality_from_another_account_cannot_be_picked(owner, other_owner):
    """Owner-scoped like every account-realm row: an id from elsewhere is not a quality."""
    theirs = (await qualities(other_owner))["qualities"][0]["id"]

    response = await owner.put("/api/profile/qualities", json={"quality_ids": [theirs]})

    assert response.status_code == 404, response.text


# --- Anchor's guess, and the moment it stops guessing ---


@SMALL
async def test_anchor_pre_ticks_its_guess_until_the_owner_answers(owner, run_jobs, provider):
    """Confirm-not-author: the owner arrives at a filled-in checklist, not a blank one."""
    provider.will_say(qualities=["Pacing", "Ending"])
    await settled(owner, run_jobs)

    shown = await qualities(owner)
    assert shown["answered"] is False
    assert ticked(shown) == ["Pacing", "Ending"]
    assert guessed(shown) == ["Pacing", "Ending"]


@SMALL
async def test_a_guess_is_not_a_constraint(owner, db, run_jobs, provider):
    """Anchor guessing is not the owner speaking, so nothing durable is written.

    The distinction is what keeps the prose honest: a constraint is an instruction every
    regeneration must respect, and a regeneration that had to respect its own guesses
    would be reading its own handwriting back as evidence.
    """
    provider.will_say(qualities=["Pacing"])
    account = await settled(owner, run_jobs)

    assert await profile_constraints(db, account) == []
    assert [row[2] for row in await prose_versions(db, account)] == ["first"]


@SMALL
async def test_the_guess_is_shown_the_owners_criteria_answers(owner, run_jobs, provider):
    """Criteria answers make the suggestions smarter over time (taste-profile.md)."""
    await ask_criteria(owner, "often")
    await settled(owner, run_jobs)
    landed, _ = await place(owner, LIBRARY[5], "b")
    assert landed["criteria"], "the bonus card was not offered"
    await answer_criteria(owner, landed["criteria"], "a")

    await build_ordering(owner, LIBRARY[6:8])
    await run_jobs()

    quality = landed["criteria"]["quality"]
    lines = provider.last_of(llm.SUGGEST_SYSTEM).prompt.user.splitlines()
    assert any(line.startswith(f"- {quality}: ") and " over " in line for line in lines), lines


@SMALL
async def test_the_guess_is_offered_the_owners_own_list_and_no_more(owner, run_jobs, provider):
    """The system never invents a quality, so it is only ever shown what to choose from."""
    await add_quality(owner, "Worldbuilding")
    await settled(owner, run_jobs)

    shown = provider.last_of(llm.SUGGEST_SYSTEM).prompt.user
    offered = [line.removeprefix("- ") for line in shown.splitlines() if line.startswith("- ")]
    assert "Worldbuilding" in offered
    assert {entry["name"] for entry in (await qualities(owner))["qualities"]} <= set(offered)


@SMALL
async def test_once_the_owner_answers_anchor_stops_guessing(owner, run_jobs, provider):
    """Their answer is the answer, so there is nothing left to guess and nothing to spend."""
    await settled(owner, run_jobs)
    await pick_qualities(owner, ["Humor"])
    guesses = len(provider.asked_of(llm.SUGGEST_SYSTEM))

    await build_ordering(owner, LIBRARY[5:8])
    await run_jobs()

    assert len(provider.asked_of(llm.SUGGEST_SYSTEM)) == guesses
    assert ticked(await qualities(owner)) == ["Humor"]


@SMALL
async def test_a_hollow_account_is_never_guessed_at(owner, db, run_jobs, provider):
    """Zero spend before the account has earned it, the picker included."""
    await place(owner, LIBRARY[0], "b")
    await run_jobs()

    assert provider.asked_of(llm.SUGGEST_SYSTEM) == []
    assert guessed(await qualities(owner)) == []


# --- The free-text escape hatch ---


async def test_free_text_adds_a_custom_quality_to_the_list(owner):
    """An escape hatch, never a requirement: it adds an entry and ticks nothing."""
    added = await add_quality(owner, "Worldbuilding")

    shown = await qualities(owner)
    assert added["origin"] == "custom"
    assert [entry["name"] for entry in shown["qualities"]][-1] == "Worldbuilding"
    assert len(shown["qualities"]) == BUILT_IN + 1
    assert ticked(shown) == []
    assert shown["answered"] is False


async def test_a_custom_quality_is_asked_like_any_other(owner, db):
    """A normal list entry, so the criteria rotation reaches it the same way (#36).

    Worked all the way round the list rather than sampled: the custom entry lands after
    everything present, so the only way to see it asked is to reach the end - and that is
    also the only way to prove it is on the rotation rather than merely on the screen.
    """
    await ask_criteria(owner, "often")
    await add_quality(owner, "Worldbuilding")

    await build_ordering(owner, ROTATION)

    asked = [entry[0] for entry in await criteria_log(db, uuid.UUID(await account_id(owner)))]
    assert asked[:BUILT_IN] == list(BUILT_IN_QUALITIES)
    assert "Worldbuilding" in asked, asked


async def test_a_quality_the_owner_already_has_is_not_added_twice(owner):
    """One list, so a name appears on it once - and typing it again is not an error."""
    added = await add_quality(owner, "  acting ")

    shown = await qualities(owner)
    assert added["name"] == "Acting"
    assert added["origin"] == "built_in"
    assert len(shown["qualities"]) == BUILT_IN


async def test_a_blank_quality_is_not_a_quality(owner):
    await add_quality(owner, "   ", expect=422)

    assert len((await qualities(owner))["qualities"]) == BUILT_IN


# --- Corrections that survive a rewrite ---


@SMALL
async def test_a_thumbed_down_claim_is_kept_as_a_row_not_as_an_edit(owner, db, run_jobs, provider):
    """Never a text edit: the version the owner read is exactly what it was."""
    provider.will_say(paragraphs=["You love a big finish."])
    account = await settled(owner, run_jobs)

    await thumb_down(owner, "You love a big finish.")

    assert [row[1] for row in await prose_versions(db, account)] == ["You love a big finish."]
    (constraint,) = await profile_constraints(db, account)
    kind, name, content, live = constraint
    assert (kind, name, live) == ("prose_correction", None, True)
    assert content["claim"] == "You love a big finish."


@SMALL
async def test_a_correction_survives_a_regeneration_that_contradicts_it(
    owner, db, run_jobs, provider
):
    """The whole reason a correction is structural rather than an edit.

    The provider is scripted to make the same claim again, which is the case an edit to
    the text could not survive: the rewrite replaces the text wholesale. The row is
    untouched by it, and the next regeneration is still told not to say it.
    """
    provider.will_say(paragraphs=["You love a big finish."])
    account = await settled(owner, run_jobs)
    await thumb_down(owner, "You love a big finish.")

    provider.will_say(paragraphs=["You love a big finish."])
    await run_jobs()

    assert [row[3] for row in await profile_constraints(db, account)] == [True]
    assert len(await prose_versions(db, account)) == 2
    shown = provider.last_of(llm.PROSE_SYSTEM).prompt.user
    assert "They have said this is wrong about them: You love a big finish." in shown


@SMALL
async def test_a_correction_rewrites_the_prose_at_once(owner, db, run_jobs):
    """A correction is the owner speaking, so it outranks any amount of unmoved ordering."""
    account = await settled(owner, run_jobs)

    await thumb_down(owner, "You love a big finish.")
    await run_jobs()

    assert [row[2] for row in await prose_versions(db, account)] == ["first", "constraints"]


@SMALL
async def test_a_lifted_correction_stops_being_respected(owner, db, run_jobs, provider):
    """Active or lifted: taking it back stops the instruction without losing the fact."""
    account = await settled(owner, run_jobs)
    correction = await thumb_down(owner, "You love a big finish.")
    await run_jobs()

    await lift_correction(owner, correction["id"])
    await run_jobs()

    assert [row[3] for row in await profile_constraints(db, account)] == [False]
    assert "wrong about them" not in provider.last_of(llm.PROSE_SYSTEM).prompt.user


@SMALL
async def test_the_profile_screen_carries_what_the_owner_corrected(owner, run_jobs):
    """Correctable means visible: an undo the owner cannot find is not an undo."""
    await settled(owner, run_jobs)
    await thumb_down(owner, "You love a big finish.")

    standing = await corrections(owner)
    assert [one["claim"] for one in standing] == ["You love a big finish."]

    await lift_correction(owner, standing[0]["id"])
    assert await corrections(owner) == []


@SMALL
async def test_a_correction_belongs_to_the_owner_who_made_it(owner, other_owner, run_jobs):
    await settled(owner, run_jobs)
    correction = await thumb_down(owner, "You love a big finish.")

    await lift_correction(other_owner, correction["id"], expect=404)

    assert len(await corrections(owner)) == 1


async def test_a_blank_correction_is_not_a_correction(owner):
    await thumb_down(owner, "   ", expect=422)

    assert await corrections(owner) == []


@SMALL
async def test_the_picker_and_the_corrections_are_the_same_kind_of_fact(owner, db, run_jobs):
    """Both are the owner speaking, so both reach a regeneration as instructions."""
    account = await settled(owner, run_jobs)

    await pick_qualities(owner, ["Pacing"])
    await thumb_down(owner, "You love a big finish.")

    kinds = [row[0] for row in await profile_constraints(db, account)]
    assert kinds == ["quality_pick", "prose_correction"]
    assert (await profile(owner))["corrections"], "the correction is not on the screen"
