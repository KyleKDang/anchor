"""Settling one film: the owner asking outright for a placement to run again.

The fourth door into a re-placement (rating-system.md), driven the way its owner drives
it - open a film that is still settling, ask for it, answer until it lands. The setting
is an imported account throughout, because that is the account the door exists for: one
where every position is a placeholder and no new placements are arriving to firm them up.

Nothing here asserts which opponent the advisory picker chose; what it asserts is how
many questions the owner had to answer, which is the whole claim the head start makes.
"""

import pytest

import export
import flows
from export import Row
from faketmdb import FilmFixture
from flows import LIBRARY, account_id, ordering_of, rated
from invariants import (
    assert_appended_only,
    assert_bands_well_formed,
    assert_no_drift,
    assert_ordering_well_formed,
    comparison_log,
    placement_trust,
)

SEEDS = tuple(
    FilmFixture(7500 + n, f"Settle {n}", release_date=f"{1995 + n}-04-01", popularity=20.0 - n)
    for n in range(6)
)
"""Six films, one per band from 5.0 down: six provisional slots with room to bisect."""

RATINGS = (5.0, 4.5, 4.0, 3.5, 3.0, 2.5)

FRESH = FilmFixture(7590, "Fresh Eyes", release_date="2021-01-01", popularity=9.0)
"""A film the owner places by hand afterwards: the double-duty opponent's source."""


@pytest.fixture(autouse=True)
def stocked(tmdb):
    """The imported seeds, the film placed by hand, and the shared dozen the scale uses."""
    return tmdb.with_films(*SEEDS, FRESH, *LIBRARY)


@pytest.fixture
async def imported(owner, stocked, run_jobs):
    """An imported library: every film rated, every position a placeholder."""
    await flows.upload_export(
        owner,
        export.export(
            ratings=tuple(
                Row(film.title, film.year, rating=rating)
                for film, rating in zip(SEEDS, RATINGS, strict=True)
            )
        ),
    )
    await run_jobs()
    return owner


# --- The door ---


async def test_a_film_still_settling_asks_nothing_until_the_owner_asks_for_it(imported):
    """The door is a mark the owner leaves, not a state read off the film.

    Every imported film is provisional, so a placement screen that opened on provisional
    alone would reopen questions on all of them at once - and would have no way to tell a
    film the owner is settling from one they walked away from.
    """
    reopened = await flows.begin(imported, SEEDS[3])

    assert reopened["done"] is True, "a film nobody asked about was asked about"
    assert reopened["position"] == 4

    await flows.ask_to_re_place(imported, SEEDS[3])
    step = await flows.begin(imported, SEEDS[3])

    assert step["done"] is False and step["kind"] == "comparison"


async def test_the_film_page_says_which_offer_it_should_make(imported, db):
    """ "Settle it now" on a provisional film, "Re-place" on a trusted one - one flag."""
    page = await flows.film_page(imported, SEEDS[2])
    assert page["provisional"] is True

    order = _ordering(await rated(imported))
    await flows.settle(imported, SEEDS[2], order, order.index(SEEDS[2].tmdb_id))

    assert (await flows.film_page(imported, SEEDS[2]))["provisional"] is False
    await assert_ordering_well_formed(db, await account_id(imported))


async def test_asking_twice_resumes_the_settle_rather_than_restarting_it(imported):
    """A second click is the same ask: the answers already given are not thrown away."""
    await flows.ask_to_re_place(imported, SEEDS[1])
    step = await flows.begin(imported, SEEDS[1])
    before = step["answered"]
    await flows.answer(imported, SEEDS[1], step["b"]["tmdb_id"], "a")

    await flows.ask_to_re_place(imported, SEEDS[1])
    resumed = await flows.begin(imported, SEEDS[1])

    assert resumed["done"] is False
    assert resumed["answered"] == before + 1, "the second ask set aside what the first collected"


async def test_a_film_that_was_never_placed_cannot_be_placed_again(owner, stocked):
    await flows.mark_watched(owner, SEEDS[0], "later")

    await flows.ask_to_re_place(owner, SEEDS[0], expect=409)


# --- The head start ---


async def test_a_film_others_have_narrowed_settles_in_fewer_answers(imported, db):
    """Every judgment a provisional film collected is an already-answered question.

    A placement the owner ran for some other film compared it against these, and each of
    those answers is evidence about the seed too - so settling one the owner has already
    judged resumes from what they said, while settling one nobody has touched starts from
    the whole ordering. The difference between the two counts is the whole claim.
    """
    account = await account_id(imported)
    await flows.place(imported, FRESH, "a")

    untouched = await _still_settling(imported, db, judged=False, interior=True)
    from_scratch = await _settle_counting_questions(imported, untouched)

    narrowed = await _still_settling(imported, db, judged=True, interior=True)
    head_started = await _settle_counting_questions(imported, narrowed)

    assert from_scratch[0]["answered"] == 0, "a film nobody had judged started from something"
    assert head_started[0]["answered"] >= 1, "the judgments it had collected were not counted"
    assert len(head_started) < len(from_scratch)
    await assert_ordering_well_formed(db, account)


async def test_a_trusted_film_asked_to_re_place_sets_its_old_answers_aside(owner, stocked, db):
    """The rewatch's seeding, reached by the owner asking instead (rating-system.md).

    The position being questioned is the one those answers produced, so re-applying them
    would re-derive the answer the owner is disputing. The film starts from its current
    slot with a clean search, and the comparisons decide from there.
    """
    await flows.build_ordering(owner, SEEDS[:4])
    order = [film.tmdb_id for film in SEEDS[:4]]
    assert (await flows.film_page(owner, SEEDS[3]))["provisional"] is False

    await flows.ask_to_re_place(owner, SEEDS[3])
    step = await flows.begin(owner, SEEDS[3])

    assert step["answered"] == 0, "the placement's own answers seeded the flow questioning it"

    # And they really are set aside: the film that lost every comparison wins them all
    # this time, and lands at the top rather than being held down by what it said before.
    await flows.replace_at(owner, SEEDS[3], order, 0)

    assert _ordering(await rated(owner))[0] == SEEDS[3].tmdb_id
    await assert_ordering_well_formed(db, await account_id(owner))


# --- Landing ---


async def test_a_settled_film_lands_trusted_and_its_judgments_are_re_read(imported, db):
    """The mark comes off, and every judgment about the film is read against the new slot.

    The re-reading is what any re-placement does (rating-system.md), and here it has the
    least to do: a search head-started by the film's own judgments never asks a question
    it has already kept, so its landing agrees with all of them and none is settled
    against. The observable claim is that nothing about the film is left in tension - the
    contradicted half of the rule is the drift flow's, and is pinned there.
    """
    account = await account_id(imported)
    await flows.place(imported, FRESH, "a")
    settling = await _still_settling(imported, db, judged=True)
    before = await comparison_log(db, account)

    landed, asked = await flows.settle(imported, settling, _ordering(await rated(imported)), 0)

    assert landed["done"] is True
    assert landed["provisional"] is False, "a film answered through is still a placeholder"
    assert _row_for(await rated(imported), settling.tmdb_id)["provisional"] is False
    assert (await placement_trust(db, account))[settling.tmdb_id] == ("full", "completed")
    await assert_no_drift(db, account, "settling a film")
    assert_appended_only(before, await comparison_log(db, account), "settling a film")
    await assert_ordering_well_formed(db, account)
    await assert_bands_well_formed(db, account)


async def test_bailing_out_mid_settle_lands_provisionally_and_reopens_nothing(owner, db):
    """Early bail is available here as anywhere, and the door closes behind it.

    The setting is a band with room inside it, because that is the only place an early
    bail means anything: the stars are settled and only the exact neighbours are open.
    The film keeps the answers it collected and stays a placeholder, which is the honest
    result - and the next visit shows where it landed rather than putting the questions
    the owner walked away from back on the screen.
    """
    account = await account_id(owner)
    ids = await flows.scale(owner, size=9, top=1, bottom=7)
    await flows.bail_inside_the_band(owner, LIBRARY[9], ids)
    assert _row_for(await rated(owner), LIBRARY[9].tmdb_id)["provisional"] is True

    await flows.ask_to_re_place(owner, LIBRARY[9])
    await flows.answer_until_the_band_locks(owner, LIBRARY[9], ids, 4)
    landed = await flows.bail(owner, LIBRARY[9])

    assert landed["provisional"] is True, "stopping early left a fully trusted position"
    reopened = await flows.begin(owner, LIBRARY[9])
    assert reopened["done"] is True, "a settle the owner bailed out of came back on reload"
    assert reopened["position"] == landed["position"]
    await assert_ordering_well_formed(db, account)
    await assert_bands_well_formed(db, account)


async def test_the_done_screen_offers_settling_another_while_more_remain(imported, db):
    """The one quiet way onward, counting what is left and never chasing it.

    Anchors are not counted: an anchor is re-placed from its own page with the warning
    that comes with it, so offering one from here would offer a film this door will not
    hand over.
    """
    order = _ordering(await rated(imported))
    await flows.designate(imported, 4.0, SEEDS[2])

    landed, _ = await flows.settle(imported, SEEDS[5], order, order.index(SEEDS[5].tmdb_id))

    payload = await rated(imported)
    rows = [film for group in payload["groups"] for slot in group["slots"] for film in slot]
    still = {film["tmdb_id"] for film in rows if film["provisional"]}
    anchored = {film["tmdb_id"] for film in rows if film["anchor"]}
    assert anchored & still, "no anchor was left settling, so the exclusion went untested"
    assert landed["settle_another"] == len(still - anchored)

    # A landing nobody asked to settle says nothing about settling at all.
    plain, _ = await flows.place(imported, FRESH, "a")
    assert plain["settle_another"] is None
    await assert_ordering_well_formed(db, await account_id(imported))


async def test_a_settle_the_owner_only_skipped_earns_no_bonus_card(imported, db):
    """The bonus card names a pair the owner just compared, and a head start is not one.

    A settle resumes from every judgment the film has collected, so what the flow holds
    can be entirely work done for other films weeks ago. Skipping through means the owner
    answered nothing here - and a card drawn from the head start would be a bonus for a
    placement that earned nothing, asking about a comparison they never saw this time.
    """
    await flows.ask_criteria(imported, "often")
    await flows.place(imported, FRESH, "a")
    film = await _still_settling(imported, db, judged=True)

    await flows.ask_to_re_place(imported, film)
    step = await flows.begin(imported, film)
    while not step["done"]:
        if step["kind"] == "band":
            step = await flows.answer_the_band(imported, film, step)
            continue
        step = await flows.answer(imported, film, step["b"]["tmdb_id"], "skip")

    assert step["criteria"] is None, "a settle the owner skipped through minted a bonus card"


# --- Helpers ---


async def _still_settling(client, db, *, judged, interior=False):
    """A provisional film other placements have judged, or one they have not touched.

    ``interior`` keeps to the middle of the ordering, where a bisection has work to do:
    the best and worst films in a list are pinned by a single answer whatever anyone has
    said about them before, so counting questions at either end would measure the
    position rather than the head start.
    """
    account = await account_id(client)
    log = await comparison_log(db, account)
    trust = await placement_trust(db, account)
    order = _ordering(await rated(client))
    middle = order[1:-1] if interior else order
    found = next(
        (
            film
            for film in SEEDS
            if film.tmdb_id in middle
            and trust.get(film.tmdb_id, ("full",))[0] == "provisional"
            and bool(_judgments_about(log, film.tmdb_id)) is judged
        ),
        None,
    )
    assert found is not None, f"no film left that is provisional and judged={judged}"
    return found


async def _settle_counting_questions(client, film):
    """Settle one film to a landing, keeping every question it had to ask on the way.

    Answered so the film lands back where it already sits, so the count reflects how much
    of the search the head start had already done rather than how far the film moved.
    """
    order = _ordering(await rated(client))
    _, asked = await flows.settle(client, film, order, order.index(film.tmdb_id))
    return asked


def _judgments_about(log, film_id):
    """Every strict comparison touching this film, whoever's flow asked for it."""
    return [row for row in log if film_id in (row[3], row[4]) and row[5] in ("a", "b", "tied")]


def _opponents_of(log, film_id):
    return {row[4] if row[3] == film_id else row[3] for row in _judgments_about(log, film_id)}


def _ordering(payload):
    """The films of the ordering, best first, one id per slot."""
    return [slot[0] for slot in ordering_of(payload)]


def _row_for(payload, tmdb_id):
    return next(
        film
        for group in payload["groups"]
        for slot in group["slots"]
        for film in slot
        if film["tmdb_id"] == tmdb_id
    )
