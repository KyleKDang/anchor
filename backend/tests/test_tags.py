"""Quality tags: what a film is known for, and what buying that answer is allowed to cost.

These read as what the owner did - place films, then place more - and assert what the
platform paid for it. Almost everything here is an assertion about money, because that is
where this feature can go wrong: the tags themselves are a small improvement to one
optional bonus question, and a tagging that re-bought an answer it already had, or bought
one for an account that has told Anchor nothing, would cost more than the feature is
worth.

The one invariant carried through the whole file is that the provider has been asked
exactly once per tagged film, ever, by anybody. It is asserted after flows of every
shape, because "once per film ever" is not a property of any single path through the
code - it is a property of the catalog, and the catalog is shared.
"""

import pytest

from anchor import llm
from anchor.models import BUILT_IN_QUALITIES
from flows import (
    LIBRARY,
    add_quality,
    ask_criteria,
    build_ordering,
    place,
    scale,
)
from invariants import account_realm_tables, quality_tags, spend_ledger, tagged_films

# Small bars, so an account reaches *forming* in four placements rather than twenty and a
# test about tagging spends its time on tagging. The dimensions are spec; the numbers are
# tuning, and every test that wants a cold account simply leaves this off.
EARNED = pytest.mark.settings(readiness_forming_films=3, readiness_forming_bands=1)


@pytest.fixture(autouse=True)
def stocked(tmdb):
    return tmdb.with_films(*LIBRARY)


SETTLED = 5
"""How many films it takes to get an ordering with two anchors in it, which is *forming*."""


async def worked(client, run_jobs):
    """An account past the *forming* bar, with the tagging its next placement earned.

    The placement after the anchors is the one that buys anything, and that is the design
    rather than an accident of this helper: an account is cold while it is being built,
    and a cold account never causes spend. Films placed on the way up are tagged later,
    when the owner's activity brings them back into a comparison.
    """
    await scale(client, size=SETTLED)
    await place(client, LIBRARY[SETTLED], "b")
    await run_jobs()


async def assert_bought_once_per_film(db, provider):
    """The invariant this whole feature stands on: one call per tagged film, ever.

    Films tagged with nothing are in the count too, which is the point of counting this
    way: a film the provider said was notable for none of the vocabulary has still been
    paid for, and it is exactly the film a re-buy would be invisible on.
    """
    assert len(provider.asked_of(llm.TAG_SYSTEM)) == len(await tagged_films(db))


async def tags_across_the_catalog(db):
    """Every tag on every film anybody has paid for, as one set."""
    return {quality for film in await tagged_films(db) for quality in await quality_tags(db, film)}


# --- Bought once, for everybody ---


@EARNED
async def test_a_film_s_tags_are_bought_once_and_then_read_from_the_catalog(
    owner, db, run_jobs, provider
):
    """Placing more films re-uses what the catalog holds rather than re-asking for it."""
    await worked(owner, run_jobs)
    tagged = await tagged_films(db)
    assert tagged, "placements past the forming bar should have tagged something"
    await assert_bought_once_per_film(db, provider)

    await place(owner, LIBRARY[SETTLED + 1], "b")
    await run_jobs()

    assert set(tagged) <= set(await tagged_films(db))
    await assert_bought_once_per_film(db, provider)


@EARNED
async def test_one_owner_s_tags_are_every_owner_s(owner, other_owner, db, run_jobs, provider):
    """A tag is a fact about the film, so the second account inherits it having paid nothing."""
    await worked(owner, run_jobs)
    bought = len(provider.asked_of(llm.TAG_SYSTEM))

    await worked(other_owner, run_jobs)

    assert len(provider.asked_of(llm.TAG_SYSTEM)) < 2 * bought, "the same films were re-tagged"
    await assert_bought_once_per_film(db, provider)


async def test_the_tags_table_belongs_to_no_account(db):
    """The shared catalog is unscoped by construction, so deleting an account takes none."""
    async with db.sessions() as session:
        assert "quality_tags" not in await account_realm_tables(session)


@EARNED
async def test_a_film_the_provider_finds_nothing_in_is_still_only_asked_about_once(
    owner, db, run_jobs, provider
):
    """The answer "notable for none of them" is an answer, and it is bought once like any."""
    await worked(owner, run_jobs)  # the fake's default answer is no qualities at all

    assert await tagged_films(db)
    assert await tags_across_the_catalog(db) == set()
    await assert_bought_once_per_film(db, provider)

    await place(owner, LIBRARY[SETTLED + 1], "b")
    await run_jobs()

    await assert_bought_once_per_film(db, provider)


# --- What it is allowed to cost ---


@EARNED
async def test_a_tag_is_billed_to_nobody_in_particular(owner, db, run_jobs):
    """Shared scope: the ledger row carries no account, because no account owns the answer."""
    await worked(owner, run_jobs)

    tagging = [row for row in await spend_ledger(db) if row[1] == "tag_film_qualities"]

    assert tagging, "the tagging should have reached the ledger"
    assert all(row[0] is None for row in tagging)


@EARNED
async def test_tagging_runs_on_the_cheap_tier_in_a_batch(owner, run_jobs, provider, settings):
    """Listwise cheap work, dispatched asynchronously: nothing on a screen is waiting."""
    await worked(owner, run_jobs)

    tagging = provider.asked_of(llm.TAG_SYSTEM)

    assert tagging
    assert all(call.model.id == settings.llm_cheap_model for call in tagging)
    assert all(call.dispatch == "batch" for call in tagging)


# Both settings in one marker: only the closest ``settings`` marker is read, so stacking
# this on ``EARNED`` would silently drop the readiness bars and test a cold account.
@pytest.mark.settings(
    readiness_forming_films=3, readiness_forming_bands=1, llm_global_monthly_cap_usd=0.0
)
async def test_a_spent_global_cap_stops_tagging_and_nothing_else_notices(
    owner, db, run_jobs, provider
):
    """The degradation is invisible: no tags, no bill, and the bonus card arrives anyway."""
    await ask_criteria(owner, "often")
    await worked(owner, run_jobs)

    landed, _ = await place(owner, LIBRARY[SETTLED + 1], "b")
    await run_jobs()

    assert provider.dispatched == 0
    assert await tagged_films(db) == []
    assert await spend_ledger(db) == []
    assert landed["criteria"] is not None


async def test_a_hollow_account_never_buys_a_tag(owner, db, run_jobs, provider):
    """The flood-of-signups case. Shared work has no account to charge, so the gate is here.

    Without this the shared scope would be a hole in "spend is earned by engagement": the
    seam's own readiness gate reads an account, and a tag deliberately has none.
    """
    await build_ordering(owner, LIBRARY[:4])  # the default bars are twenty films away
    await run_jobs()

    assert len(provider.asked_of(llm.TAG_SYSTEM)) == 0
    assert await tagged_films(db) == []


@EARNED
async def test_turning_the_questions_off_does_not_leave_the_library_untagged(
    owner, db, run_jobs, provider
):
    """The frequency setting governs the card, not the catalog.

    An owner who has turned the after-a-placement card off has said something about their
    own screen, not about a film: their tags still serve every other account, and they
    still serve the film page's question session, which is available whatever the
    frequency says. Buying them on their setting would leave a library that no later
    feature could ever tag.
    """
    await ask_criteria(owner, "off")

    await worked(owner, run_jobs)

    assert await tagged_films(db)
    await assert_bought_once_per_film(db, provider)


# --- The built-in vocabulary, and nothing else ---


@EARNED
async def test_only_the_built_in_vocabulary_is_ever_stored_as_a_tag(owner, db, run_jobs, provider):
    """A provider that answers something nobody offered is answering about nothing."""
    provider.will_say(qualities=["Tension", "Costumes", "Vibes"])

    await worked(owner, run_jobs)

    stored = await tags_across_the_catalog(db)
    assert stored == {"Tension"}
    assert stored <= set(BUILT_IN_QUALITIES)


@EARNED
async def test_an_answer_nobody_can_read_is_paid_for_once_and_not_again(
    owner, db, run_jobs, provider
):
    """The one shape this could quietly run up a bill in: an unusable answer, re-bought.

    The tokens are spent before the answer is parsed, so a film left untagged by a
    malformed one would go back in front of the next placement that touches it, and the
    next. It is stamped instead - known for nothing, which the fallback already copes
    with - and the prompt bug is reported rather than paid for again.
    """
    provider.will_say_exactly("sorry, I would rather not", system=llm.TAG_SYSTEM)

    await worked(owner, run_jobs)
    bought = len(provider.asked_of(llm.TAG_SYSTEM))

    await place(owner, LIBRARY[SETTLED + 1], "b")
    await run_jobs()

    assert len(provider.asked_of(llm.TAG_SYSTEM)) > bought, "the next film should still be tagged"
    await assert_bought_once_per_film(db, provider)


@EARNED
async def test_a_quality_the_owner_invented_is_never_tagged(owner, db, run_jobs, provider):
    """A custom quality is one account's word for something, so it cannot be a fact about a film.

    That is what makes tags shareable at all, and it is why the rotation is the only route
    a custom quality has to a criteria question - which the rotation test below walks.
    """
    await add_quality(owner, "Costumes")
    provider.will_say(qualities=["Costumes"])

    await worked(owner, run_jobs)

    assert await tagged_films(db)
    assert await tags_across_the_catalog(db) == set()
