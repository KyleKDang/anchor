"""Drift, re-placement, and rewatches: what happens when the owner changes their mind.

These read as the owner's flows - keep comparing until something contradicts, answer the
quiet check that follows, resolve the flag that surfaces, re-place, log a rewatch - and
assert what the owner can see: what the film page offers, what the Rated strip collects,
which films the placement flow will still measure against, and above all that nothing
moved on its own. Which film the advisory math flagged is asserted where it is the
owner's business (they are asked about it) and never how it decided.

The hard wall runs through every test here: a contradiction is stored, never applied.
"""

import pytest

from flows import (
    LIBRARY,
    account_id,
    answer,
    answer_band,
    answer_pair,
    answer_rewatch,
    begin,
    build_ordering,
    designate,
    film_page,
    flag_of,
    keep_comparing,
    keep_position,
    log_rewatch,
    mark_watched,
    ordering_of,
    rated,
    re_place,
    replace_at,
)
from invariants import anchors as anchor_rows
from invariants import (
    assert_appended_only,
    assert_no_drift,
    assert_ordering_well_formed,
    comparison_log,
    drift_flags,
    in_tension,
    open_flags,
    ordering_snapshot,
    statuses,
)


@pytest.fixture(autouse=True)
def stocked(tmdb):
    return tmdb.with_films(*LIBRARY)


async def contradicted(client, index=3):
    """Make the owner contradict themselves, which is where every flag here starts.

    Five films placed worst-last, so each one lost to everything above it and the
    ordering is the placement order. Then the owner extends one film's placement and
    says the opposite of what they said the first time: the film moves, because that is
    what keep-comparing is for - and the older judgment it just contradicted does not.
    """
    films = LIBRARY[:5]
    await build_ordering(client, films)
    step = await keep_comparing(client, films[index])
    assert step["kind"] == "comparison", step
    opponent = step["b"]["tmdb_id"]
    await answer(client, films[index], opponent, "a")
    return [film.tmdb_id for film in films], opponent


async def answer_check(client, film, step, *, against):
    """Answer the pair a quiet check showed, deliberately for or against the ordering.

    Which of the two films the flag landed on is the advisory math's business and not a
    test's (testing.md), so nothing here assumes it. The ordering is read instead, and
    the verdict is chosen to say either "the one you have lower is better" - another
    contradiction - or the opposite, which clears the suspicion.
    """
    at = {entry["tmdb_id"]: entry["position"] for entry in _films_of(await rated(client))}
    a, b = step["a"]["tmdb_id"], step["b"]["tmdb_id"]
    a_ranks_higher = at[a] < at[b]
    a_wins = (not a_ranks_higher) if against else a_ranks_higher
    return await answer_pair(client, film, a, b, "a" if a_wins else "b")


async def offered_check(client, film):
    """Place a film until the quiet check rides in, and hand back the step showing it."""
    await mark_watched(client, film, "now")
    step = await begin(client, film)
    while not step["done"] and step["kind"] == "comparison":
        if film.tmdb_id not in (step["a"]["tmdb_id"], step["b"]["tmdb_id"]):
            return step
        step = await answer_pair(client, film, step["a"]["tmdb_id"], step["b"]["tmdb_id"], "b")
    raise AssertionError("no drift check was offered during the placement")


# --- The hard wall ---


async def test_a_contradicting_answer_is_stored_in_tension_and_reorders_nothing(owner, db):
    """ADR 0001's wall: the engine may notice a contradiction and may not act on it."""
    account = await account_id(owner)
    ids, opponent = await contradicted(owner)
    before = await ordering_snapshot(db, account)
    log = await comparison_log(db, account)

    # The quiet check puts the same pair again, inside another film's placement.
    step = await offered_check(owner, LIBRARY[5])
    checked = {step["a"]["tmdb_id"], step["b"]["tmdb_id"]}

    await answer_check(owner, LIBRARY[5], step, against=True)

    assert await ordering_snapshot(db, account) == before, "an answer moved the ordering"
    assert_appended_only(log, await comparison_log(db, account), "the drift check")
    assert frozenset(checked) in await in_tension(db, account)
    assert ids and opponent
    await assert_ordering_well_formed(db, account)


async def test_one_flag_per_film_aggregates_every_judgment_implicating_it(owner, db):
    """One doubt, resolved once: a second flag could only ever say the same thing."""
    account = await account_id(owner)
    _, flagged = await surfaced(owner, db)

    page = await film_page(owner, _film(flagged))

    assert list(await open_flags(db, account)) == [flagged], "one film, and one flag on it"
    assert len(await drift_flags(db, account)) == 1
    assert len(page["drift"]["judgments"]) == 2, "both contradictions hang on the one flag"
    await assert_ordering_well_formed(db, account)


async def test_a_flag_whose_evidence_resolves_itself_closes_itself(owner, db):
    """Nothing left to stand on: the owner is never asked about a doubt that went away.

    The spec's own example - the opponent moves, and the judgments become consistent. So
    the owner never touches the flagged film at all: they rewatch the *other* one, change
    their mind about it, and re-place it to the side the old judgments always said it
    belonged on. The flag closes without anybody having answered it.
    """
    account = await account_id(owner)
    ids, flagged = await surfaced(owner, db)
    judgment = (await flag_of(owner, _film(flagged)))["judgments"][0]
    opponent = judgment["opponent"]["tmdb_id"]
    others = [i for i in ids if i != opponent]

    await log_rewatch(owner, _film(opponent))
    await answer_rewatch(owner, _film(opponent), "changed")
    # Consistency is whichever side the old judgments put the opponent on.
    await replace_at(owner, _film(opponent), others, 0 if judgment["opponent_won"] else len(others))

    assert await open_flags(db, account) == {}, "the doubt about the other film went away"
    assert "self_resolved" in [outcome for *_, outcome in await drift_flags(db, account)]
    assert await in_tension(db, account) == set()
    await assert_ordering_well_formed(db, account)


# --- The quiet phase ---


async def test_a_quiet_check_is_indistinguishable_from_a_normal_comparison(owner, db):
    """No label, no marker, no extra field: an owner who knows is answering the test."""
    await contradicted(owner)

    await mark_watched(owner, LIBRARY[5], "now")
    normal = await begin(owner, LIBRARY[5])
    step = await answer(owner, LIBRARY[5], normal["b"]["tmdb_id"], "b")

    assert step["kind"] == "comparison"
    assert LIBRARY[5].tmdb_id not in {step["a"]["tmdb_id"], step["b"]["tmdb_id"]}
    assert set(step) == set(normal), "the check carries a field a normal question does not"
    assert await open_flags(db, await account_id(owner))


async def test_a_placement_carries_at_most_one_drift_check(owner, db):
    """One favour per placement: the owner opened this to place their own film."""
    await contradicted(owner)
    account = await account_id(owner)

    await mark_watched(owner, LIBRARY[5], "now")
    step = await begin(owner, LIBRARY[5])
    checks = 0
    while not step["done"] and step["kind"] == "comparison":
        pair = (step["a"]["tmdb_id"], step["b"]["tmdb_id"])
        if LIBRARY[5].tmdb_id not in pair:
            checks += 1
        step = await answer_pair(owner, LIBRARY[5], pair[0], pair[1], "b")

    assert checks == 1
    await assert_ordering_well_formed(db, account)


async def test_a_probed_suspicion_is_not_probed_again(owner, db):
    """Asked once, folded in, and left alone: re-asking on the same evidence is nagging."""
    await contradicted(owner)

    step = await offered_check(owner, LIBRARY[5])
    asked = {step["a"]["tmdb_id"], step["b"]["tmdb_id"]}
    await answer_check(owner, LIBRARY[5], step, against=False)

    # A second placement gets no check about that pair, whatever else it is offered.
    await mark_watched(owner, LIBRARY[6], "now")
    step = await begin(owner, LIBRARY[6])
    seen = []
    while not step["done"] and step["kind"] == "comparison":
        seen.append({step["a"]["tmdb_id"], step["b"]["tmdb_id"]})
        step = await answer_pair(owner, LIBRARY[6], step["a"]["tmdb_id"], step["b"]["tmdb_id"], "b")

    assert asked not in seen


async def test_a_check_that_clears_the_suspicion_leaves_the_flag_quiet(owner, db):
    """The quiet phase exists to be wrong: an answer that agrees closes nothing loudly."""
    account = await account_id(owner)
    await contradicted(owner)

    step = await offered_check(owner, LIBRARY[5])
    # Answer the way the ordering already reads, which confirms nothing is wrong.
    await answer_check(owner, LIBRARY[5], step, against=False)

    assert set((await open_flags(db, account)).values()) <= {"quiet"}
    assert (await rated(owner))["needs_attention"] == []


# --- The loud phase ---


async def surfaced(client, db):
    """Push a suspicion past what noise explains, so the owner is finally asked."""
    ids, _ = await contradicted(client)
    step = await offered_check(client, LIBRARY[5])
    # Say it again: one contradiction is a slip of the finger, two is a pattern.
    await answer_check(client, LIBRARY[5], step, against=True)
    loud = [
        film
        for film, stage in (await open_flags(db, await account_id(client))).items()
        if stage == "surfaced"
    ]
    assert loud, "two contradictions should have surfaced a flag"
    return ids, loud[0]


async def test_a_surfaced_flag_reaches_the_strip_and_the_film_page(owner, db):
    """Its two homes, and nowhere else: no push, no modal, no dot (ADR 0011)."""
    _, flagged = await surfaced(owner, db)

    screen = await rated(owner)
    page = await film_page(owner, _film(flagged))

    assert [film["tmdb_id"] for film in screen["needs_attention"]] == [flagged]
    assert page["drift"] is not None
    assert page["drift"]["judgments"], "the flag shows what it stands on"
    assert "dot" not in screen and "notifications" not in screen


async def test_a_surfaced_film_is_benched_as_an_opponent(owner, db):
    """A doubted position is a bent ruler, so nothing else is measured against it."""
    _, flagged = await surfaced(owner, db)

    await mark_watched(owner, LIBRARY[6], "now")
    step = await begin(owner, LIBRARY[6])
    offered = []
    while not step["done"] and step["kind"] == "comparison":
        offered.append(step["b"]["tmdb_id"])
        step = await answer_pair(owner, LIBRARY[6], step["a"]["tmdb_id"], step["b"]["tmdb_id"], "b")

    assert flagged not in offered


async def test_rated_gains_the_has_open_drift_flag_filter(owner, db):
    """The filter shows the surfaced ones only: a quiet flag is not the owner's business yet."""
    _, flagged = await surfaced(owner, db)

    filtered = await rated(owner, flagged=True)

    assert [slot[0] for slot in ordering_of(filtered)] == [flagged]


# --- Resolution: keeping the position ---


async def test_keeping_the_position_supersedes_the_noise_and_closes_the_flag(owner, db):
    """Noise, says the owner: the position stands, and the log records that they said so."""
    account = await account_id(owner)
    _, flagged = await surfaced(owner, db)
    before = await ordering_snapshot(db, account)
    log = await comparison_log(db, account)

    await keep_position(owner, _film(flagged))

    assert await open_flags(db, account) == {}
    assert [outcome for *_, outcome in await drift_flags(db, account)] == ["kept"]
    assert await in_tension(db, account) == set()
    assert_appended_only(log, await comparison_log(db, account), "keeping the position")
    assert any("superseded" in seen for seen in (await statuses(db, account)).values())
    assert await ordering_snapshot(db, account) == before, "keeping a position moves nothing"
    assert (await rated(owner))["needs_attention"] == []


async def test_re_pointing_hands_the_tension_to_the_opponent(owner, db):
    """ "The other one is the misplaced one": the judgment stands, and moves to its flag."""
    account = await account_id(owner)
    _, flagged = await surfaced(owner, db)
    page = await film_page(owner, _film(flagged))
    opponent = page["drift"]["judgments"][0]["opponent"]["tmdb_id"]

    await keep_position(
        owner, _film(flagged), [{"opponent_tmdb_id": opponent, "resolution": "re_point"}]
    )

    open_now = await open_flags(db, account)
    assert flagged not in open_now
    assert opponent in open_now, "the tension went to the film the owner blamed"
    assert [outcome for *_, outcome in await drift_flags(db, account) if outcome] == ["re_pointed"]
    assert await in_tension(db, account), "a re-pointed judgment is still in tension"


# --- Resolution: re-placing ---


async def test_re_placement_seeds_from_the_evidence_and_closes_the_flag(owner, db):
    """The evidence is already-answered questions, so the search resumes from what it implies."""
    account = await account_id(owner)
    ids, flagged = await surfaced(owner, db)
    log = await comparison_log(db, account)

    await re_place(owner, _film(flagged))
    step = await begin(owner, _film(flagged))
    assert step["done"] is False, "re-placing asks rather than showing where it already sits"
    # The head start, said out loud: the flow opens partway through its own count,
    # because the in-tension judgments are questions the owner has already answered.
    assert step["answered"] > 0, "the re-placement started from scratch"
    await replace_at(owner, _film(flagged), [i for i in ids if i != flagged], 0)

    assert await open_flags(db, account) == {}
    assert [outcome for *_, outcome in await drift_flags(db, account)] == ["re_placed"]
    assert await in_tension(db, account) == set(), "nothing is left in tension after landing"
    assert_appended_only(log, await comparison_log(db, account), "the re-placement")
    await assert_ordering_well_formed(db, account)


async def test_re_placement_flips_its_judgments_to_active_or_superseded(owner, db):
    """Every judgment about the film is re-read against the position the owner just gave it."""
    account = await account_id(owner)
    ids, flagged = await surfaced(owner, db)

    await re_place(owner, _film(flagged))
    await replace_at(owner, _film(flagged), [i for i in ids if i != flagged], 0)

    touching = [seen for pair, seen in (await statuses(db, account)).items() if flagged in pair]
    assert touching, "the film has judgments to re-read"
    assert all("in_tension" not in seen for seen in touching)


async def test_a_drift_flag_on_an_anchor_warns_upfront_and_then_auto_retires_it(owner, db):
    """A canonical 4.0 living among the 3.5s is a contradiction in terms, so the status goes.

    The warning is the whole of the protection: the anchor is not spared the re-placement
    and the re-placement is not steered to save it. The position the owner's answers earn
    is the position it keeps, and only the designation is given up.
    """
    ids, flagged = await surfaced(owner, db)
    await designate(owner, 4.0, _film(flagged))
    page = await film_page(owner, _film(flagged))
    assert page["drift"]["anchor_warning"] is True, "the offer warns before it is taken"
    assert page["anchor"] is True

    await re_place(owner, _film(flagged))
    # Land it at the bottom, which is nowhere near the band it was the exemplar of.
    others = [i for i in ids if i != flagged]
    await replace_at(owner, _film(flagged), others, len(others))

    assert (await film_page(owner, _film(flagged)))["anchor"] is False
    assert flagged not in (await anchor_rows(db, await account_id(owner))).values()
    await assert_ordering_well_formed(db, await account_id(owner))


# --- Rewatches ---


async def test_a_rewatch_timestamps_the_watch_and_asks_one_light_question(owner, db):
    """Offer, never force: the watch is history, and the question is optional."""
    account = await account_id(owner)
    await build_ordering(owner, LIBRARY[:3])
    before = await ordering_snapshot(db, account)

    page = await log_rewatch(owner, LIBRARY[1])

    assert page["state"] == "rated", "watching it again is not a judgment about it"
    assert page["rewatch"] is not None
    assert await ordering_snapshot(db, account) == before
    await assert_no_drift(db, account, "a rewatch")


async def test_confirming_a_rewatch_keeps_the_position(owner, db):
    account = await account_id(owner)
    await build_ordering(owner, LIBRARY[:3])
    before = await ordering_snapshot(db, account)
    await log_rewatch(owner, LIBRARY[1])

    await answer_rewatch(owner, LIBRARY[1], "confirmed")

    assert (await film_page(owner, LIBRARY[1]))["rewatch"] is None, "asked once, not chased"
    assert await ordering_snapshot(db, account) == before
    await assert_no_drift(db, account, "confirming a rewatch")


async def test_changing_your_mind_at_a_rewatch_enters_a_re_placement(owner, db):
    """Seeded from the current slot, with no in-tension evidence anywhere in this path."""
    account = await account_id(owner)
    ids = [film.tmdb_id for film in LIBRARY[:4]]
    await build_ordering(owner, LIBRARY[:4])
    await log_rewatch(owner, LIBRARY[3])

    await answer_rewatch(owner, LIBRARY[3], "changed")
    step = await begin(owner, LIBRARY[3])
    assert step["done"] is False, "the film page's re-place offer opened the flow"
    # No in-tension evidence exists in this path, so there is nothing to head-start from
    # and the count opens at zero - which is what makes the drift path's count mean something.
    assert step["answered"] == 0
    await replace_at(owner, LIBRARY[3], [i for i in ids if i != LIBRARY[3].tmdb_id], 0)

    assert [slot[0] for slot in ordering_of(await rated(owner))][0] == LIBRARY[3].tmdb_id
    assert await in_tension(db, account) == set()
    await assert_ordering_well_formed(db, account)


async def test_an_open_flag_surfaces_at_the_rewatch_moment(owner, db):
    """The one moment the owner is already thinking about this exact film."""
    account = await account_id(owner)
    ids, _ = await contradicted(owner)
    quiet = [film for film, stage in (await open_flags(db, account)).items() if stage == "quiet"]
    assert quiet, "the contradiction should have raised a quiet flag"

    page = await log_rewatch(owner, _film(quiet[0]))

    assert page["drift"] is not None, "the flag came out at the rewatch rather than at random"
    assert [film["tmdb_id"] for film in (await rated(owner))["needs_attention"]] == [quiet[0]]
    assert ids


# --- The one that is not drift ---


async def test_a_rating_flip_caused_by_a_divider_move_raises_no_flag_and_no_tension(owner, db):
    """Derivation staying honest: the scale moved, and nobody changed places."""
    account = await account_id(owner)
    # Six films with two 3.5s between the anchors, so the 3.5 band still has an exemplar
    # to stand for it once the film being asked about is lifted out of the question.
    films = LIBRARY[:6]
    await build_ordering(owner, films)
    await designate(owner, 4.0, films[1])
    await designate(owner, 3.0, films[4])
    before = await ordering_snapshot(db, account)
    was = _bands_of(await rated(owner))
    assert was[films[2].tmdb_id] == 3.5, "the film starts between the two anchors"

    # The band-edge question the owner is offered *is* the divider's mover: answering
    # "this is really a 4.0" carries the 4.0/3.5 boundary down past the film.
    step = await keep_comparing(owner, films[2])
    assert step["kind"] == "band", step
    exemplar = next(option["exemplar"] for option in step["options"] if option["band"] == 4.0)
    await answer_band(owner, films[2], 4.0, exemplar["tmdb_id"])

    now = _bands_of(await rated(owner))
    assert now[films[2].tmdb_id] == 4.0, "the rating flipped, which is what makes this the case"
    assert await ordering_snapshot(db, account) == before, "a divider move reorders nobody"
    await assert_no_drift(db, account, "a divider move")


# --- Helpers ---


def _film(tmdb_id):
    """The fixture for a film id, so a test can act on what a flag named."""
    return next(film for film in LIBRARY if film.tmdb_id == tmdb_id)


def _films_of(payload):
    return [film for group in payload["groups"] for slot in group["slots"] for film in slot]


def _bands_of(payload):
    return {film["tmdb_id"]: film["band"] for film in _films_of(payload)}
