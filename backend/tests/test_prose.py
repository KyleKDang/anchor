"""The prose profile: when Anchor rewrites what it thinks you like, and what you see of it.

These read as what the owner did - place films, designate an anchor, answer a bonus
question, open Profile - and assert what it cost and what appeared. The provider is
scripted at the seam, so the prose itself is whatever the test said it would be; what is
under test is everything around it: whether a regeneration happened at all, what it was
shown, and what the screen carries afterwards.

The thing asserted most often here is that nothing happened. A regeneration is money, so
the design's real claim is negative - never per comparison, never for a hollow account,
never past a cap - and a test suite that only checked the happy path would pass just as
well against an engine that regenerated on every answer.
"""

import uuid

import pytest

from flows import (
    LIBRARY,
    account_id,
    add_constraint,
    answer_criteria,
    ask_criteria,
    build_ordering,
    designate,
    place,
    profile,
    scale,
)
from invariants import assert_versions_monotonic, prose_versions, spend_ledger

# Small bars all round, so a test spends five placements rather than thirty on saying
# something that is true at any size. The dimensions are spec; the numbers are tuning.
SMALL = pytest.mark.settings(
    readiness_forming_films=3,
    readiness_forming_bands=1,
    prose_placements_trigger=2,
    prose_drift_trigger=1,
    prose_staleness_comparisons=8,
)


@pytest.fixture(autouse=True)
def stocked(tmdb):
    return tmdb.with_films(*LIBRARY)


async def settled(client, run_jobs, size=5):
    """An account with an ordering, its anchors set, and its first prose written."""
    await scale(client, size=size)
    await run_jobs()
    return uuid.UUID(await account_id(client))


# --- Zero spend before an account has earned it ---


async def test_a_hollow_account_costs_nothing(owner, db, run_jobs, provider):
    """The flood-of-signups case: an account that never says anything never bills."""
    await place(owner, LIBRARY[0], "b")
    await run_jobs()

    assert await spend_ledger(db) == []
    assert provider.dispatched == 0
    assert await prose_versions(db, uuid.UUID(await account_id(owner))) == []


async def test_a_hollow_account_has_no_prose_on_its_profile(owner, run_jobs):
    """Nothing is shown, and nothing promises one is coming: the section is simply absent."""
    await place(owner, LIBRARY[0], "b")
    await run_jobs()

    assert (await profile(owner))["prose"] is None


# --- The first regeneration ---


@SMALL
async def test_reaching_forming_earns_the_first_prose(owner, db, run_jobs, provider):
    provider.will_say(paragraphs=["You go for films that take their time."])
    account = await settled(owner, run_jobs)

    (version,) = await prose_versions(db, account)
    assert version[0] == 1
    assert version[1] == "You go for films that take their time."
    assert version[2] == "first"


@SMALL
async def test_the_prose_appears_on_the_profile_screen_with_when_it_was_written(
    owner, run_jobs, provider
):
    """One ambient last-updated line is the whole of what the owner is told (surfacing.md)."""
    provider.will_say(paragraphs=["You go for films that take their time."])
    await settled(owner, run_jobs)

    shown = (await profile(owner))["prose"]

    assert shown["text"] == "You go for films that take their time."
    assert shown["version"] == 1
    assert shown["generated_at"]


@SMALL
async def test_the_screen_never_narrates_what_the_engine_is_doing(owner, run_jobs):
    """No trigger, no watermark, no "refreshing": the engine's work stays the engine's."""
    await settled(owner, run_jobs)

    shown = (await profile(owner))["prose"]

    assert set(shown) == {"text", "version", "generated_at"}


@SMALL
async def test_one_regeneration_lands_however_many_were_queued(owner, db, run_jobs, provider):
    """Every retrain on the way to forming queues one; only the first has work to do."""
    await settled(owner, run_jobs)

    assert len(await prose_versions(db, uuid.UUID(await account_id(owner)))) == 1
    assert provider.dispatched == 1


# --- Never per comparison ---


@SMALL
async def test_a_single_placement_does_not_rewrite_the_prose(owner, db, run_jobs, provider):
    """The whole point of accumulated change: one answer is never worth a provider call."""
    account = await settled(owner, run_jobs)
    spent = provider.dispatched

    await place(owner, LIBRARY[5], "b")
    await run_jobs()

    assert provider.dispatched == spent
    assert len(await prose_versions(db, account)) == 1


@SMALL
async def test_enough_placements_do(owner, db, run_jobs, provider):
    account = await settled(owner, run_jobs)

    await build_ordering(owner, LIBRARY[5:7])
    await run_jobs()

    versions = await prose_versions(db, account)
    assert len(versions) == 2
    assert versions[1][2] == "placements"
    assert_versions_monotonic(versions)


@SMALL
async def test_designating_an_anchor_rewrites_the_prose_at_once(owner, db, run_jobs):
    """An anchor is the owner saying what a rating means to them; that is not incremental."""
    account = await settled(owner, run_jobs)

    await designate(owner, 5.0, LIBRARY[0])
    await run_jobs()

    versions = await prose_versions(db, account)
    assert [row[2] for row in versions] == ["first", "anchors"]


@SMALL
async def test_a_picker_selection_rewrites_the_prose_at_once(owner, db, jobs_app, run_jobs):
    """A constraint is a fact the owner stated outright, so the prose owes them a rewrite.

    Nothing is placed here on purpose: a constraint edit moves no film, so it cannot lean
    on the retrain that every ordering change schedules, and this is what proves it does
    not have to.
    """
    account = await settled(owner, run_jobs)

    await add_constraint(db, jobs_app, account, "Pacing")
    await run_jobs()

    assert [row[2] for row in await prose_versions(db, account)] == ["first", "constraints"]


# --- What a regeneration is shown ---


@SMALL
async def test_a_regeneration_respects_the_owners_active_constraints(
    owner, db, jobs_app, run_jobs, provider
):
    """Structural, so a rewrite can never clobber a correction (taste-profile.md)."""
    account = await settled(owner, run_jobs)

    await add_constraint(db, jobs_app, account, "Pacing")
    await run_jobs()

    assert "They have said they care about: Pacing" in provider.last.prompt.user


@SMALL
async def test_criteria_answers_feed_the_regeneration_as_evidence(owner, db, run_jobs, provider):
    """ADR 0007: a bonus answer is loose evidence about taste, and this is where it lands."""
    await ask_criteria(owner, "often")
    account = await settled(owner, run_jobs)
    landed, _ = await place(owner, LIBRARY[5], "b")
    assert landed["criteria"], "the bonus card was not offered"
    await answer_criteria(owner, landed["criteria"], "a")

    await build_ordering(owner, LIBRARY[6:8])
    await run_jobs()

    assert len(await prose_versions(db, account)) == 2
    quality = landed["criteria"]["quality"]
    lines = provider.last.prompt.user.splitlines()
    assert any(line.startswith(f"- {quality}: ") and " over " in line for line in lines), lines


@SMALL
async def test_an_unanswered_bonus_card_says_nothing_about_anybody(owner, run_jobs, provider):
    """A skip is the owner declining to judge, so it is not evidence of anything."""
    await ask_criteria(owner, "often")
    await settled(owner, run_jobs)
    landed, _ = await place(owner, LIBRARY[5], "b")
    assert landed["criteria"], "the bonus card was not offered"

    await build_ordering(owner, LIBRARY[6:8])
    await run_jobs()

    quality = landed["criteria"]["quality"]
    lines = provider.last.prompt.user.splitlines()
    assert not any(line.startswith(f"- {quality}: ") for line in lines), lines


# --- Degrading rather than breaking ---


@SMALL
@pytest.mark.settings(
    readiness_forming_films=3,
    readiness_forming_bands=1,
    prose_placements_trigger=2,
    llm_account_monthly_cap_usd=0.001,
)
async def test_a_spent_cap_leaves_the_prose_the_owner_already_had(owner, db, run_jobs, provider):
    """Never a broken screen: the cached version stays live and says nothing about it."""
    provider.will_say(paragraphs=["The one you already had."]).costs(
        input_tokens=1_000_000, output_tokens=0
    )
    account = await settled(owner, run_jobs)

    await build_ordering(owner, LIBRARY[5:8])
    await run_jobs()

    assert len(await prose_versions(db, account)) == 1
    assert (await profile(owner))["prose"]["text"] == "The one you already had."


@SMALL
async def test_a_provider_that_is_down_leaves_the_prose_alone(owner, db, run_jobs, provider):
    from anchor import llm

    account = await settled(owner, run_jobs)
    provider.will_fail(llm.ProviderUnavailable("down"))

    await build_ordering(owner, LIBRARY[5:7])
    await run_jobs()

    assert len(await prose_versions(db, account)) == 1


@SMALL
async def test_the_version_a_regeneration_lands_is_what_discovery_will_cache_against(
    owner, db, run_jobs
):
    """Monotonic per account, so the bump is the cache invalidation (data-model.md)."""
    account = await settled(owner, run_jobs)

    await build_ordering(owner, LIBRARY[5:7])
    await run_jobs()
    await designate(owner, 5.0, LIBRARY[0])
    await run_jobs()

    assert_versions_monotonic(await prose_versions(db, account))


@SMALL
async def test_one_owners_prose_is_not_another_s(owner, other_owner, db, run_jobs):
    mine = await settled(owner, run_jobs)
    theirs = uuid.UUID(await account_id(other_owner))

    assert len(await prose_versions(db, mine)) == 1
    assert await prose_versions(db, theirs) == []
    assert (await profile(other_owner))["prose"] is None
