"""The LLM seam: who may be dispatched to, who has earned it, and what it all costs.

These run at the operations seam with a scripted adapter (testing.md), which is the one
place in Anchor where the assertions are about money rather than about the owner. The
seam's own work is never faked - the allowlist, the readiness gate, both caps, the ledger
row and the schema check all run for real - so what a test scripts is only what the
provider said back.

The rules under test are the ones ADR 0003 and ADR 0004 make structural: taste data goes
only to providers whose terms bar training on it, a hollow account costs nothing, and
neither cap can be spent past. Everything they refuse is a skip that leaves cached
results standing, never an error an owner could see.
"""

import uuid

import pytest

from anchor import llm
from anchor.models import LlmOperation
from flows import account_id, scale
from invariants import spend_ledger
from library import film

FORMING = pytest.mark.settings(readiness_forming_films=3, readiness_forming_bands=1)

VOCABULARY = ("Acting", "Screenplay", "Pacing")

CANDIDATES = (
    llm.Candidate(1, "One", 1999, ("Drama",), ("A Director",), "A film."),
    llm.Candidate(2, "Two", 2001, ("Comedy",), ("B Director",), "Another film."),
)


@pytest.fixture(autouse=True)
def stocked(tmdb):
    from flows import LIBRARY

    return tmdb.with_films(*LIBRARY)


async def formed(client, db):
    """An account past cold, so its work is worth spending on."""
    await scale(client, size=5)
    return uuid.UUID(await account_id(client))


def evidence():
    from anchor.prose import Evidence

    return Evidence(
        anchors=["4.0 stars: Film 01 (1981)"],
        loved=["Film 00 (1980)"],
        disliked=["Film 04 (1984)"],
        criteria=["Screenplay: Film 00 (1980) over Film 04 (1984)"],
        constraints=["They have said they care about: Pacing"],
        rated_films=5,
        explicit_comparisons=9,
    )


# --- The no-training provider allowlist (ADR 0003) ---


async def test_a_provider_that_trains_on_its_inputs_cannot_be_built_into_the_seam(db, settings):
    """The allowlist is enforced in code at the seam, not in a deployment checklist."""
    from fakellm import FakeLlm

    with pytest.raises(llm.ProviderRefused):
        llm.Llm(FakeLlm(provider="gemini_free"), db, settings)


@FORMING
async def test_a_provider_that_trains_on_its_inputs_cannot_be_dispatched_to(
    owner, db, settings, provider
):
    """Checked again on the way out, so the refusal is not a property of one constructor."""
    account = await formed(owner, db)
    seam = llm.Llm(provider, db, settings)
    provider.provider = "voyage"

    with pytest.raises(llm.ProviderRefused):
        await seam.regenerate_prose_profile(account, evidence())

    assert provider.dispatched == 0
    assert await spend_ledger(db) == []


async def test_the_allowlist_is_exactly_the_providers_the_research_cleared():
    """A name added without the terms check is the bug this pins (ADR 0003)."""
    assert set(llm.ALLOWED_PROVIDERS) == {"anthropic", "openai", "gemini_paid"}


# --- Spend is earned by engagement ---


async def test_a_cold_account_is_never_spent_on(owner, db, seam, provider):
    """A hollow account costs nothing: the flood-of-signups case ADR 0004 designs for."""
    account = uuid.UUID(await account_id(owner))

    with pytest.raises(llm.NotEarned):
        await seam.regenerate_prose_profile(account, evidence())

    assert provider.dispatched == 0
    assert await spend_ledger(db) == []


@FORMING
async def test_an_account_past_cold_is_spent_on(owner, db, seam, provider):
    account = await formed(owner, db)

    await seam.regenerate_prose_profile(account, evidence())

    assert provider.dispatched == 1


async def test_shared_work_is_not_gated_on_any_account_being_ready(owner, db, seam, provider):
    """A quality tag is a fact about a film, so no account's readiness bears on it."""
    provider.will_say(qualities=["Acting"])

    tags = await seam.tag_film_qualities(film(9001), VOCABULARY)

    assert tags == ["Acting"]
    assert [row[0] for row in await spend_ledger(db)] == [None]


# --- The ledger ---


@FORMING
async def test_every_call_writes_a_ledger_row(owner, db, seam, provider):
    account = await formed(owner, db)
    provider.costs(input_tokens=3000, output_tokens=400)

    await seam.regenerate_prose_profile(account, evidence())

    (row,) = await spend_ledger(db)
    scope, operation, model, input_tokens, output_tokens, cost = row
    assert scope == account
    assert operation == LlmOperation.regenerate_prose_profile
    assert model == "claude-sonnet-5"
    assert (input_tokens, output_tokens) == (3000, 400)
    # $2/Mtok in and $10/Mtok out: a price per million tokens is already the per-token
    # cost in millionths of a dollar, so the arithmetic is the tokens at those prices.
    assert cost == 3000 * 2 + 400 * 10


@FORMING
async def test_an_unusable_answer_still_costs_what_it_cost(owner, db, seam, provider):
    """The tokens were bought whether or not the answer was worth anything."""
    account = await formed(owner, db)
    provider.will_say_exactly("sorry, I would rather not")

    with pytest.raises(llm.BadAnswer):
        await seam.regenerate_prose_profile(account, evidence())

    assert len(await spend_ledger(db)) == 1


@FORMING
async def test_a_call_that_never_reached_a_provider_costs_nothing(owner, db, seam, provider):
    account = await formed(owner, db)
    provider.will_fail(llm.ProviderUnavailable("down"))

    with pytest.raises(llm.Skipped):
        await seam.regenerate_prose_profile(account, evidence())

    assert await spend_ledger(db) == []


# --- The two caps ---


@FORMING
@pytest.mark.settings(readiness_forming_films=3, readiness_forming_bands=1)
async def test_the_per_account_cap_stops_that_account_and_nobody_else(
    owner, other_owner, db, seam, provider
):
    """One account's month is over; the platform's is not."""
    mine = await formed(owner, db)
    theirs = await formed(other_owner, db)
    provider.costs(input_tokens=1_000_000, output_tokens=0)

    await seam.regenerate_prose_profile(mine, evidence())
    with pytest.raises(llm.CapReached):
        await seam.regenerate_prose_profile(mine, evidence())

    assert await seam.regenerate_prose_profile(theirs, evidence())
    assert len(await spend_ledger(db, mine)) == 1


@FORMING
@pytest.mark.settings(
    readiness_forming_films=3,
    readiness_forming_bands=1,
    llm_account_monthly_cap_usd=100.0,
    llm_global_monthly_cap_usd=2.0,
)
async def test_the_global_cap_stops_every_account(owner, other_owner, db, seam, provider):
    """A cap the platform hit is not one account's problem to be spared."""
    mine = await formed(owner, db)
    theirs = await formed(other_owner, db)
    provider.costs(input_tokens=2_000_000, output_tokens=0)

    await seam.regenerate_prose_profile(mine, evidence())

    with pytest.raises(llm.CapReached):
        await seam.regenerate_prose_profile(theirs, evidence())


@FORMING
@pytest.mark.settings(
    readiness_forming_films=3, readiness_forming_bands=1, llm_global_monthly_cap_usd=1.0
)
async def test_shared_work_counts_against_the_global_cap(db, seam, provider, owner):
    """Tags are nobody's account and everybody's bill."""
    account = await formed(owner, db)
    provider.costs(input_tokens=2_000_000, output_tokens=0).will_say(qualities=[])

    await seam.tag_film_qualities(film(9001), VOCABULARY)

    with pytest.raises(llm.CapReached):
        await seam.regenerate_prose_profile(account, evidence())


@FORMING
async def test_a_spent_cap_costs_nothing_further(owner, db, seam, provider):
    """Checked before dispatch, so a spent cap buys nothing more, not even one call."""
    account = await formed(owner, db)
    provider.costs(input_tokens=1_000_000, output_tokens=1_000_000)

    await seam.regenerate_prose_profile(account, evidence())
    with pytest.raises(llm.CapReached):
        await seam.regenerate_prose_profile(account, evidence())

    assert provider.dispatched == 1


# --- Tiers and dispatch, all config ---


@FORMING
async def test_prose_runs_on_the_mid_tier(owner, db, seam, provider):
    account = await formed(owner, db)

    await seam.regenerate_prose_profile(account, evidence())

    assert provider.last.model.id == "claude-sonnet-5"
    assert provider.last.dispatch is llm.Dispatch.immediate


@FORMING
async def test_reranking_and_tags_run_on_the_cheap_tier_in_batches(owner, db, seam, provider):
    """The two operations that fan out over many films, at half price and asynchronously."""
    account = await formed(owner, db)
    provider.will_say(ranked=[]).will_say(qualities=[])

    await seam.rerank_candidates(account, "you like slow films", CANDIDATES)
    await seam.tag_film_qualities(film(9001), VOCABULARY)

    assert [asked.model.id for asked in provider.asked] == ["claude-haiku-4-5"] * 2
    assert [asked.dispatch for asked in provider.asked] == [llm.Dispatch.batch] * 2


@FORMING
@pytest.mark.settings(
    readiness_forming_films=3,
    readiness_forming_bands=1,
    llm_mid_tier_operations="",
    llm_batched_operations="regenerate_prose_profile",
    llm_cheap_model="a-cheaper-model",
)
async def test_which_tier_and_which_dispatch_are_configuration(owner, db, seam, provider):
    """Moving an operation between tiers is an environment variable, not a code change."""
    account = await formed(owner, db)

    await seam.regenerate_prose_profile(account, evidence())

    assert provider.last.model.id == "a-cheaper-model"
    assert provider.last.dispatch is llm.Dispatch.batch


@FORMING
@pytest.mark.settings(
    readiness_forming_films=3,
    readiness_forming_bands=1,
    llm_batched_operations="regenerate_prose_profile",
)
async def test_a_batched_call_is_charged_at_half(owner, db, seam, provider):
    account = await formed(owner, db)
    provider.costs(input_tokens=1000, output_tokens=100)

    await seam.regenerate_prose_profile(account, evidence())

    (row,) = await spend_ledger(db)
    assert row[5] == (1000 * 2 + 100 * 10) // 2


# --- Answers are schema-validated, and bounded by what was offered ---


@FORMING
async def test_the_prose_comes_back_as_paragraphs(owner, db, seam, provider):
    account = await formed(owner, db)
    provider.will_say(paragraphs=["First.", "Second."])

    assert await seam.regenerate_prose_profile(account, evidence()) == "First.\n\nSecond."


@FORMING
async def test_an_answer_of_the_wrong_shape_is_refused(owner, db, seam, provider):
    """Schema-validated at the seam, so nothing malformed reaches a table or a screen."""
    account = await formed(owner, db)
    provider.will_say(paragraphs="not a list of them")

    with pytest.raises(llm.BadAnswer):
        await seam.regenerate_prose_profile(account, evidence())


@FORMING
async def test_a_ranking_may_only_contain_films_that_were_offered(owner, db, seam, provider):
    """An invented film would be a suggestion nobody could act on."""
    account = await formed(owner, db)
    provider.will_say(
        ranked=[
            {"tmdb_id": 2, "explanation": "for you"},
            {"tmdb_id": 999, "explanation": "invented"},
            {"tmdb_id": 2, "explanation": "said twice"},
            {"tmdb_id": 1, "explanation": "also for you"},
        ]
    )

    ranked = await seam.rerank_candidates(account, "you like slow films", CANDIDATES)

    assert [candidate.tmdb_id for candidate in ranked] == [2, 1]


async def test_a_tag_may_only_name_a_quality_in_the_vocabulary(db, seam, provider, owner):
    """The system never invents a quality (taste-profile.md); it only ever picks one."""
    provider.will_say(qualities=["Acting", "Vibes", "acting", "Pacing"])

    assert await seam.tag_film_qualities(film(9001), VOCABULARY) == ["Acting", "Pacing"]


@FORMING
async def test_a_suggestion_may_only_name_a_quality_on_the_owners_list(owner, db, seam, provider):
    account = await formed(owner, db)
    provider.will_say(qualities=["Pacing", "Something they never added"])

    assert await seam.suggest_qualities(account, evidence(), VOCABULARY) == ["Pacing"]


# --- What the operations are actually shown ---


@FORMING
async def test_a_regeneration_is_shown_the_owners_anchors_answers_and_constraints(
    owner, db, seam, provider
):
    """The evidence lines are the whole of what a regeneration may say something from."""
    account = await formed(owner, db)

    await seam.regenerate_prose_profile(account, evidence())

    shown = provider.last.prompt.user
    assert "4.0 stars: Film 01 (1981)" in shown
    assert "Screenplay: Film 00 (1980) over Film 04 (1984)" in shown
    assert "They have said they care about: Pacing" in shown
    assert "5 film(s)" in shown


@FORMING
async def test_a_regeneration_is_told_not_to_produce_anything_rating_shaped(
    owner, db, seam, provider
):
    """ADR 0005 keeps predicted ratings off every surface, this one included."""
    account = await formed(owner, db)

    await seam.regenerate_prose_profile(account, evidence())

    assert "No ratings, no scores, no numbers" in provider.last.prompt.system
