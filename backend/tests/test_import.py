"""The seed import, driven the way its owner drives it: upload, resolve, live with it.

Every test speaks the JSON API over a real database with TMDB faked at its HTTP edge,
per testing.md. The export fixtures carry the real 592-row export's awkward rows - a
non-breaking space after an en dash, a middle dot, commas inside quoted titles, accents
- alongside the synthetic ones no real export contained: a missing year, a TV-side row,
a deleted film, a duplicate title and year.
"""

import uuid

import pytest
from sqlalchemy import text

import export
import flows
from anchor import seeding
from export import Row
from faketmdb import FilmFixture
from flows import account_id
from invariants import (
    account_realm_tables,
    assert_bands_derived,
    assert_bands_well_formed,
    assert_ordering_well_formed,
    assert_seeded_slots_only_shrank,
    comparison_log,
    dividers,
    last_synced_ratings,
    placement_trust,
    seeded_slots,
    watch_clock,
    watch_events,
)
from invariants import (
    anchors as anchor_rows,
)

BANDS = (5.0, 4.5, 4.0, 3.5, 3.0, 2.5, 2.0, 1.5, 1.0, 0.5)
"""The ten half-star values, best first: Letterboxd's scale and Anchor's, 1:1."""

SEEDS = tuple(
    FilmFixture(7000 + n, f"Seed {n:02d}", release_date=f"{1990 + n}-06-01", popularity=30.0 + n)
    for n in range(10)
)
"""One rated film per band, so a full export pins all nine dividers."""

TWIN = FilmFixture(7100, "Seed 04 Also", release_date="2010-01-01", popularity=12.0)
"""A second film at 3.0, so one band's provisional tie-group holds two."""

WANTED = FilmFixture(7200, "Wanted Someday", release_date="2021-01-01", popularity=8.0)
"""Only on the watchlist: the seeded backlog."""

SEEN_ONLY = FilmFixture(7300, "Seen But Unrated", release_date="2019-01-01", popularity=9.0)
"""Watched with no rating: watched-unrated, with a rate-later seat."""


@pytest.fixture
def stocked(tmdb):
    return tmdb.with_films(*SEEDS, TWIN, WANTED, SEEN_ONLY)


def _rated_rows():
    """One ratings.csv row per band, plus the twin that shares 3.0 with Seed 04."""
    rows = [
        Row(film.title, film.year, rating=band) for film, band in zip(SEEDS, BANDS, strict=True)
    ]
    return (*rows, Row(TWIN.title, TWIN.year, rating=3.0))


async def test_the_export_becomes_one_row_per_line_that_matters(owner):
    """Five files are read; everything else in the archive is discarded unread.

    The archive the fixture builds carries reviews, comments, likes, and the deleted and
    orphaned folders whose ``diary.csv`` is identical in shape to the real one - so a
    parser matching on a file's name rather than its place in the archive shows up here
    as diary rows nobody watched.
    """
    data = export.export(
        ratings=(Row("Arrival", 2016, rating=4.5), Row("Fight Club", 1999, rating=3.0)),
        watchlist=(Row("Nosferatu", 1922),),
        watched=(Row("Arrival", 2016), Row("Fight Club", 1999)),
        diary=(Row("Arrival", 2016, watched_date="2024-04-02"),),
        favorites=("Arrival",),
    )
    await flows.upload_export(owner, data)

    state = await flows.import_state(owner)
    assert state["source_name"] == export.NAME
    assert state["counts"] == {
        "rating": 2,
        "watchlist": 1,
        "watched": 2,
        "diary": 1,
        "profile_favorite": 1,
    }


async def test_an_archive_that_is_not_an_export_is_refused(owner):
    """A zip holding none of the five files is not an export, and is told so.

    Refused outright rather than accepted as an import of zero rows: an owner who
    uploaded the wrong file is owed the news, not a silent success.
    """
    await flows.upload_export(
        owner,
        export.export(
            omit=("ratings.csv", "watchlist.csv", "watched.csv", "diary.csv", "profile.csv")
        ),
        expect=422,
    )
    assert (await flows.import_state(owner))["status"] == "none"


async def test_profile_pii_is_discarded_unread(owner, db):
    """profile.csv yields favourites and nothing else; the rest is PII with no product use."""
    data = export.export(favorites=("Arrival",))
    await flows.upload_export(owner, data)

    rows = await _import_rows(db, await owner_id(owner))
    assert [row["name"] for row in rows] == ["Arrival"]
    assert export.EMAIL not in repr(rows)


def _slots(payload):
    """The ordering as sorted slot membership.

    Within a slot the films are tied, so whatever order the screen happens to list them
    in says nothing an assertion should hold it to.
    """
    return [sorted(slot) for slot in flows.ordering_of(payload)]


async def owner_id(client):
    return uuid.UUID(await account_id(client))


async def _import_rows(db, account):
    async with db.sessions() as session:
        result = await session.execute(
            text(
                """
                SELECT kind, name, year, rating, rewatch, state
                FROM import_rows WHERE account_id = :id ORDER BY kind, name
                """
            ),
            {"id": account},
        )
        return [dict(row._mapping) for row in result]


# --- The whole thing, end to end ---


async def test_a_full_export_seeds_the_library_the_owner_already_recognises(
    owner, stocked, db, run_jobs
):
    """One import, and the account has its ratings, its backlog, and its history.

    The whole of the first acceptance criterion in one flow, because that is how the
    owner meets it: ratings become provisional tie-groups pinning the dividers so the
    familiar half-stars show at once, watchlist rows seed the backlog, a watched row
    with no rating takes a rate-later seat, diary rows become watch events, and
    profile.csv yields a favourite and nothing else.
    """
    account = await owner_id(owner)
    await flows.upload_export(
        owner,
        export.export(
            ratings=_rated_rows(),
            watchlist=(Row(WANTED.title, WANTED.year),),
            watched=(Row(SEEN_ONLY.title, SEEN_ONLY.year), Row(SEEDS[0].title, SEEDS[0].year)),
            diary=(
                Row(SEEDS[0].title, SEEDS[0].year, watched_date="2024-04-02"),
                Row(SEEDS[1].title, SEEDS[1].year, watched_date="2024-04-03", rewatch=True),
            ),
            favorites=(SEEDS[0].title,),
        ),
    )
    await run_jobs()

    state = await flows.import_state(owner)
    assert state["status"] == "complete"
    assert (state["pending"], state["review_pending"], state["unmatched"]) == (0, 0, 0)

    payload = await flows.rated(owner)
    # Ten tie-groups, best to worst, one per half-star value - and the eleventh rating
    # joins the group its value already has rather than being ordered inside it.
    assert flows.ordering_of(payload) == [
        *[[film.tmdb_id] for film in SEEDS[:4]],
        sorted([SEEDS[4].tmdb_id, TWIN.tmdb_id]),
        *[[film.tmdb_id] for film in SEEDS[5:]],
    ]
    assert flows.bands_of(payload) == {
        **{film.tmdb_id: band for film, band in zip(SEEDS, BANDS, strict=True)},
        TWIN.tmdb_id: 3.0,
    }
    assert all(
        film["provisional"]
        for group in payload["groups"]
        for slot in group["slots"]
        for film in slot
    )

    # All nine dividers pinned, because every band came out of the export holding a film.
    assert sorted(await dividers(db, account)) == sorted(BANDS[:-1])
    await assert_bands_derived(db, account, flows.bands_of(payload))
    await assert_bands_well_formed(db, account)
    await assert_ordering_well_formed(db, account)

    # The watchlist seeds the backlog; the film rated in the same import does not join it.
    assert [film["tmdb_id"] for film in (await flows.backlog(owner))["films"]] == [WANTED.tmdb_id]

    # Watched with no rating: outside the ordering, holding a rate-later seat.
    assert flows.queue_of(payload) == [SEEN_ONLY.tmdb_id]

    # Diary rows are watch events, rewatch flags and all, and they count into the clock.
    assert await watch_clock(db, account) == 2
    assert [
        (film, str(origin), rewatch) for film, _, origin, rewatch in await watch_events(db, account)
    ] == [
        (SEEDS[0].tmdb_id, "import_seeded", False),
        (SEEDS[1].tmdb_id, "import_seeded", True),
    ]

    # Every seeded placement is provisional, and says the import put it there.
    assert set(await placement_trust(db, account)) == {
        *(film.tmdb_id for film in SEEDS),
        TWIN.tmdb_id,
    }
    assert set((await placement_trust(db, account)).values()) == {("provisional", "import_seeded")}

    # The sync list's baseline: what Letterboxd holds, as far as Anchor knows.
    assert await last_synced_ratings(db, account) == {
        **{film.tmdb_id: band for film, band in zip(SEEDS, BANDS, strict=True)},
        TWIN.tmdb_id: 3.0,
    }


async def test_no_within_band_order_is_fabricated(owner, stocked, db, run_jobs):
    """Two films rated the same value share one slot; nothing puts one above the other."""
    await flows.upload_export(
        owner,
        export.export(
            ratings=(
                Row(SEEDS[4].title, SEEDS[4].year, rating=3.0),
                Row(TWIN.title, TWIN.year, rating=3.0),
            )
        ),
    )
    await run_jobs()

    assert _slots(await flows.rated(owner)) == [sorted([SEEDS[4].tmdb_id, TWIN.tmdb_id])]
    assert await seeded_slots(db, await owner_id(owner)) == [
        sorted([SEEDS[4].tmdb_id, TWIN.tmdb_id])
    ]


# --- What the matcher will and will not decide ---

EMPIRE = FilmFixture(1891, "Star Wars: Episode V - The Empire Strikes Back", "1980-05-17")
WALL_E = FilmFixture(10681, "WALL-E", "2008-06-22")
MONSTERS = FilmFixture(585, "Monsters, Inc.", "2001-11-01")
LEON = FilmFixture(101, "Leon: The Professional", "1994-09-14")
FESTIVAL = FilmFixture(7400, "Festival Premiere", "2017-03-01")
"""TMDB dates it to the wide release; Letterboxd dates it to the premiere a year before."""

LONELY = FilmFixture(7500, "Singular Title", None, popularity=3.0)
"""Unique on TMDB, and exported with no year: the popularity rule is all there is."""

CROWD_PLEASER = FilmFixture(7600, "Common Title", "1995-01-01", popularity=200.0)
ALSO_RAN = FilmFixture(7601, "Common Title", "1974-01-01", popularity=2.0)
"""One landslide: nobody would pick the other, so the matcher does not ask."""

REMAKE = FilmFixture(7700, "Twin Title", "2001-01-01", popularity=20.0)
ORIGINAL = FilmFixture(7701, "Twin Title", "2001-01-01", popularity=15.0)
"""Same title, same year, comparable standing: exactly what must not be decided alone."""

EDGE_FILMS = (
    EMPIRE,
    WALL_E,
    MONSTERS,
    LEON,
    FESTIVAL,
    LONELY,
    CROWD_PLEASER,
    ALSO_RAN,
    REMAKE,
    ORIGINAL,
)

NBSP = " "
EN_DASH = "–"


@pytest.fixture
def edges(tmdb):
    return tmdb.with_films(*EDGE_FILMS)


async def test_the_matcher_accepts_only_the_rows_nobody_would_argue_about(owner, edges, run_jobs):
    """The awkward rows a real export supplied, and the ones only a synthetic one can.

    Every row here is auto-accepted, each by one of the two rules: a normalized title
    plus a year that leaves exactly one candidate, retried at plus and minus one for the
    festival-versus-release disagreement; or a lone exact-title hit, which is what
    carries the row whose year is missing.
    """
    rows = (
        # A non-breaking space after an en dash, straight out of the real export.
        Row(f"Star Wars: Episode V {EN_DASH}{NBSP}The Empire Strikes Back", 1980, rating=5.0),
        Row("WALL·E", 2008, rating=4.5),  # middle dot against TMDB's hyphen
        Row("Monsters, Inc.", 2001, rating=4.0),  # a comma inside a quoted title
        Row("Léon: The Professional", 1994, rating=3.5),  # accents
        Row("Festival Premiere", 2016, rating=3.0),  # a year off by one
        Row("Singular Title", None, rating=2.5),  # no year at all
        Row("Common Title", None, rating=2.0),  # a popularity landslide
    )
    await flows.upload_export(owner, export.export(ratings=rows))
    await run_jobs()

    state = await flows.import_state(owner)
    assert (state["pending"], state["review_pending"], state["unmatched"]) == (0, 0, 0)
    assert flows.bands_of(await flows.rated(owner)) == {
        EMPIRE.tmdb_id: 5.0,
        WALL_E.tmdb_id: 4.5,
        MONSTERS.tmdb_id: 4.0,
        LEON.tmdb_id: 3.5,
        FESTIVAL.tmdb_id: 3.0,
        LONELY.tmdb_id: 2.5,
        CROWD_PLEASER.tmdb_id: 2.0,
    }


async def test_two_films_of_one_name_are_a_question_the_owner_answers(owner, edges, run_jobs):
    """A duplicate title and year with no landslide between them queues to review.

    Ranked by popularity, with the poster, year and director that tell them apart -
    which is why a candidate costs its bundled TMDB call and a search hit does not.
    """
    await flows.upload_export(owner, export.export(ratings=(Row("Twin Title", 2001, rating=4.0),)))
    await run_jobs()

    assert (await flows.import_state(owner))["review_pending"] == 1
    (row,) = (await flows.review_queue(owner))["rows"]
    assert (row["name"], row["year"], row["rating"]) == ("Twin Title", 2001, 4.0)
    assert [candidate["tmdb_id"] for candidate in row["candidates"]] == [
        REMAKE.tmdb_id,
        ORIGINAL.tmdb_id,
    ]
    assert row["candidates"][0]["directors"] == ["David Fincher"]

    # Nothing has happened to the account: a row waiting on an answer affects nothing.
    assert flows.ordering_of(await flows.rated(owner)) == []


async def test_a_row_with_no_film_behind_it_waits_indefinitely(owner, edges, run_jobs):
    """TV-side entries and deleted films are structurally unmatchable, not failures.

    ``/search/movie`` never returns either, so the two are indistinguishable here and
    both land in the same place: an open row that affects nothing until the owner binds
    a film by hand or gives up on it.
    """
    await flows.upload_export(
        owner,
        export.export(
            ratings=(
                Row("Some Miniseries Letterboxd Hosts", 2019, rating=4.0),
                Row("A Film Since Deleted", 2003, rating=2.0),
            )
        ),
    )
    await run_jobs()

    state = await flows.import_state(owner)
    assert (state["unmatched"], state["pending"], state["review_pending"]) == (2, 0, 0)
    assert [row["name"] for row in (await flows.unmatched(owner))["rows"]] == [
        "A Film Since Deleted",
        "Some Miniseries Letterboxd Hosts",
    ]
    assert flows.ordering_of(await flows.rated(owner)) == []


# --- Resolving what is left ---

TWIN_URI = "https://boxd.it/twin1"
LOST_URI = "https://boxd.it/lost1"


async def test_the_owner_binds_a_review_row_and_it_takes_effect_at_once(owner, edges, run_jobs):
    """Picking a candidate applies the row there and then; there is no batch to finish."""
    await flows.upload_export(owner, export.export(ratings=(Row("Twin Title", 2001, rating=4.0),)))
    await run_jobs()
    (row,) = (await flows.review_queue(owner))["rows"]

    bound = await flows.bind_row(owner, row["id"], ORIGINAL.tmdb_id)
    assert bound["film"]["tmdb_id"] == ORIGINAL.tmdb_id
    assert flows.bands_of(await flows.rated(owner)) == {ORIGINAL.tmdb_id: 4.0}
    assert (await flows.review_queue(owner))["rows"] == []

    # It is bound now, so answering it again is refused rather than quietly re-rating.
    await flows.bind_row(owner, row["id"], REMAKE.tmdb_id, expect=409)


async def test_an_unmatched_row_binds_through_a_search_or_is_given_up_on(owner, edges, run_jobs):
    """The two ways off the unmatched list: name the film by hand, or dismiss the row."""
    await flows.upload_export(
        owner,
        export.export(
            ratings=(Row("A Film Since Deleted", 2003, rating=2.0),),
            watchlist=(Row("Another Ghost", 1998),),
        ),
    )
    await run_jobs()
    rows = {row["name"]: row["id"] for row in (await flows.unmatched(owner))["rows"]}
    assert set(rows) == {"A Film Since Deleted", "Another Ghost"}

    await flows.bind_row(owner, rows["A Film Since Deleted"], LONELY.tmdb_id)
    await flows.dismiss_row(owner, rows["Another Ghost"])

    assert (await flows.unmatched(owner))["rows"] == []
    assert flows.bands_of(await flows.rated(owner)) == {LONELY.tmdb_id: 2.0}
    # Dismissed for good: it is off the list and can no longer be answered.
    assert (await flows.backlog(owner))["films"] == []
    await flows.bind_row(owner, rows["Another Ghost"], WANTED.tmdb_id, expect=409)


async def test_the_letterboxd_rescue_resolves_one_row_at_a_time(owner, edges, letterboxd, run_jobs):
    """The rescue follows this row's own short link and reads the id off the film page."""
    letterboxd.resolving(TWIN_URI, ORIGINAL.tmdb_id)
    await flows.upload_export(
        owner, export.export(ratings=(Row("Twin Title", 2001, rating=4.0, uri=TWIN_URI),))
    )
    await run_jobs()
    (row,) = (await flows.review_queue(owner))["rows"]
    assert row["rescuable"]

    bound = await flows.rescue_row(owner, row["id"])
    assert bound["film"]["tmdb_id"] == ORIGINAL.tmdb_id
    assert flows.bands_of(await flows.rated(owner)) == {ORIGINAL.tmdb_id: 4.0}
    # One row, one request: nothing here walks the queue.
    assert len(letterboxd.requests) == 1


async def test_the_rescue_fails_without_taking_the_row_with_it(owner, edges, letterboxd, run_jobs):
    """A TV-side entry and a 403 are both expected, and neither disturbs the row."""
    letterboxd.as_series(TWIN_URI, 1399)
    await flows.upload_export(
        owner,
        export.export(
            ratings=(
                Row("Twin Title", 2001, rating=4.0, uri=TWIN_URI),
                Row("A Film Since Deleted", 2003, rating=2.0, uri=LOST_URI),
            )
        ),
    )
    await run_jobs()
    review = {row["name"]: row["id"] for row in (await flows.review_queue(owner))["rows"]}
    lost = {row["name"]: row["id"] for row in (await flows.unmatched(owner))["rows"]}

    await flows.rescue_row(owner, review["Twin Title"], expect=502)
    letterboxd.forbidden = True
    await flows.rescue_row(owner, lost["A Film Since Deleted"], expect=502)

    # Both rows are exactly where they were, still answerable another way.
    assert [row["name"] for row in (await flows.review_queue(owner))["rows"]] == ["Twin Title"]
    assert [row["name"] for row in (await flows.unmatched(owner))["rows"]] == [
        "A Film Since Deleted"
    ]


@pytest.mark.settings(letterboxd_rescue_rate_limit=1)
async def test_the_rescue_is_throttled(owner, edges, letterboxd, run_jobs):
    """Throttled and never bulk: it is a button beside one row, not a pass over the queue."""
    letterboxd.resolving(TWIN_URI, ORIGINAL.tmdb_id)
    await flows.upload_export(
        owner,
        export.export(
            ratings=(
                Row("Twin Title", 2001, rating=4.0, uri=TWIN_URI),
                Row("A Film Since Deleted", 2003, rating=2.0, uri=LOST_URI),
            )
        ),
    )
    await run_jobs()
    (review,) = (await flows.review_queue(owner))["rows"]
    (lost,) = (await flows.unmatched(owner))["rows"]

    await flows.rescue_row(owner, review["id"])
    await flows.rescue_row(owner, lost["id"], expect=429)
    assert len(letterboxd.requests) == 1


# --- Re-import is a hard reset ---


async def test_the_wipe_covers_every_table_an_account_owns(db):
    """The reset's table list is declared, so a table added later fails here first.

    Structural on purpose. A wipe that discovers its own scope at runtime would quietly
    keep working while leaving a new table's rows behind, and the realm invariant would
    hold right up until somebody noticed their old comparisons had survived a reset.
    """
    async with db.sessions() as session:
        owned = set(await account_realm_tables(session))
    assert owned == set(seeding.WIPED) | set(seeding.KEPT)


async def test_the_warning_enumerates_what_it_is_about_to_destroy(owner, stocked, run_jobs):
    """Counted, not described: "50 ratings, 200 comparisons" is something to weigh."""
    await flows.upload_export(
        owner,
        export.export(
            ratings=_rated_rows()[:3],
            watchlist=(Row(WANTED.title, WANTED.year),),
            diary=(Row(SEEDS[0].title, SEEDS[0].year),),
        ),
    )
    await run_jobs()
    await flows.designate(owner, 5.0, SEEDS[0])

    warning = await flows.reset_warning(owner)
    assert (warning["rated_films"], warning["backlog_films"], warning["watch_events"]) == (3, 1, 1)
    assert warning["anchors"] == 1
    # Nothing has been answered yet, so the counts carry the whole warning by themselves.
    assert (warning["comparisons"], warning["confirmation_required"]) == (0, False)


@pytest.mark.settings(import_reset_confirm_comparisons=0)
async def test_a_re_import_over_answered_comparisons_makes_the_owner_type(owner, stocked, run_jobs):
    """Once the owner has actually answered questions, the log is worth stopping for."""
    await flows.upload_export(owner, export.export(ratings=_rated_rows()[:3]))
    await run_jobs()
    await flows.place(owner, SEEN_ONLY, "b")

    warning = await flows.reset_warning(owner)
    assert warning["comparisons"] > 0
    assert warning["confirmation_required"]

    again = export.export(ratings=(Row(WANTED.title, WANTED.year, rating=1.0),))
    await flows.upload_export(owner, again, expect=409)
    await flows.upload_export(owner, again, confirm="not the phrase", expect=409)
    # Everything is still there: a refused reset destroys nothing.
    assert len(flows.ordering_of(await flows.rated(owner))) == 4

    await flows.upload_export(owner, again, confirm=warning["confirmation_phrase"])


async def test_a_re_import_rebuilds_from_the_new_export_alone(owner, stocked, db, run_jobs):
    """No merge path, ever: everything the account held goes, seeded and organic alike."""
    account = await owner_id(owner)
    await flows.upload_export(owner, export.export(ratings=_rated_rows()[:3]))
    await run_jobs()
    await flows.designate(owner, 5.0, SEEDS[0])
    await flows.add_to_backlog(owner, WANTED)  # hand-added, and it goes too

    await flows.upload_export(
        owner,
        export.export(
            ratings=(Row(SEEN_ONLY.title, SEEN_ONLY.year, rating=2.0),),
            watchlist=(Row(TWIN.title, TWIN.year),),
        ),
    )
    await run_jobs()

    assert flows.ordering_of(await flows.rated(owner)) == [[SEEN_ONLY.tmdb_id]]
    assert flows.bands_of(await flows.rated(owner)) == {SEEN_ONLY.tmdb_id: 2.0}
    assert [film["tmdb_id"] for film in (await flows.backlog(owner))["films"]] == [TWIN.tmdb_id]
    assert await anchor_rows(db, account) == {}

    # The one exception to the comparison log's never-deleted rule: only the new export's
    # own band judgments remain, and every one of them was made by this import.
    log = await comparison_log(db, account)
    assert {str(entry[6]) for entry in log} == {"seed_import"}
    assert len(log) == 1
    await assert_ordering_well_formed(db, account)

    # The owner is still logged in on the other side of it: a session is not account data.
    assert (await flows.import_state(owner))["counts"]["rating"] == 1


# --- The provisional lifecycle, as it applies to seeded films ---

TRIO = tuple(
    FilmFixture(7800 + n, f"Trio {n}", release_date=f"{2000 + n}-01-01", popularity=5.0)
    for n in range(3)
)
"""Three films rated the same value: one provisional tie-group with room to shrink."""


@pytest.fixture
def trio(tmdb):
    return tmdb.with_films(*TRIO, *SEEDS, SEEN_ONLY)


async def test_an_imported_film_is_an_opponent_and_the_answer_is_its_first_evidence(
    owner, trio, db, run_jobs
):
    """Post-import there is nothing else to compare against, so seeds are the opponents.

    And a comparison run for another film's placement is evidence about the seed too: it
    is what pulls a seeded position towards a settled one without ever asking the owner
    an extra question. Here one placement between two seeds settles both of them, and
    their placements graduate - still recording that the import is what put them there.
    """
    account = await owner_id(owner)
    await flows.upload_export(
        owner,
        export.export(
            ratings=(
                Row(SEEDS[0].title, SEEDS[0].year, rating=5.0),
                Row(SEEDS[1].title, SEEDS[1].year, rating=3.0),
            )
        ),
    )
    await run_jobs()
    assert set((await placement_trust(db, account)).values()) == {("provisional", "import_seeded")}

    await flows.place_at(owner, SEEN_ONLY, [SEEDS[0].tmdb_id, SEEDS[1].tmdb_id], 1)

    trust = await placement_trust(db, account)
    assert trust[SEEDS[0].tmdb_id] == ("full", "import_seeded")
    assert trust[SEEDS[1].tmdb_id] == ("full", "import_seeded")
    assert trust[SEEN_ONLY.tmdb_id] == ("full", "completed")


async def test_a_tie_against_a_seeded_film_pulls_it_out_of_its_group(owner, trio, db, run_jobs):
    """Provisional membership is never inherited, so the seed comes out to meet the film.

    The owner judged one film equal to one other film. Joining the group would assert it
    equal to two more it was never compared with, which is the within-band order the
    whole design refuses to invent - so the tie opens a definitive two-film slot at the
    position the seed already held, and the group it left only shrinks.
    """
    account = await owner_id(owner)
    await flows.upload_export(
        owner, export.export(ratings=tuple(Row(film.title, film.year, rating=4.0) for film in TRIO))
    )
    await run_jobs()
    before = await seeded_slots(db, account)
    assert before == [sorted(film.tmdb_id for film in TRIO)]

    await flows.mark_watched(owner, SEEN_ONLY, "now")
    step = await flows.begin(owner, SEEN_ONLY)
    opponent = step["b"]["tmdb_id"]
    await flows.answer(owner, SEEN_ONLY, opponent, "tied")

    others = sorted(film.tmdb_id for film in TRIO if film.tmdb_id != opponent)
    payload = await flows.rated(owner)
    assert _slots(payload) == [sorted([opponent, SEEN_ONLY.tmdb_id]), others]
    # Nothing changed band: both slots are still the 4.0 the export said they were.
    assert set(flows.bands_of(payload).values()) == {4.0}

    trust = await placement_trust(db, account)
    assert trust[opponent] == ("full", "import_seeded")
    assert trust[SEEN_ONLY.tmdb_id] == ("full", "completed")
    assert {trust[film] for film in others} == {("provisional", "import_seeded")}

    assert_seeded_slots_only_shrank(before, await seeded_slots(db, account))
    await assert_ordering_well_formed(db, account)
    await assert_bands_well_formed(db, account)


async def test_one_film_is_one_question_however_many_lines_named_it(owner, edges, run_jobs):
    """A film rated, watched and logged is three lines the matcher failed on identically.

    The owner is answering "which film is this?", not "which film is this line?", so the
    screen asks once and the answer lands on every line naming it. Asking three times
    would make a real export's review queue three times the work for no more information.
    """
    ambiguous = Row("Twin Title", 2001, rating=4.0)
    await flows.upload_export(
        owner,
        export.export(
            ratings=(ambiguous,),
            watched=(ambiguous,),
            diary=(Row("Twin Title", 2001, watched_date="2024-03-01"),),
        ),
    )
    await run_jobs()

    assert (await flows.import_state(owner))["review_pending"] == 1
    (row,) = (await flows.review_queue(owner))["rows"]

    await flows.bind_row(owner, row["id"], ORIGINAL.tmdb_id)
    state = await flows.import_state(owner)
    assert (state["review_pending"], state["unmatched"]) == (0, 0)
    # All three lines took effect: the rating placed it, and the diary line is a watch.
    assert flows.bands_of(await flows.rated(owner)) == {ORIGINAL.tmdb_id: 4.0}


async def test_dismissing_a_film_dismisses_every_line_that_named_it(owner, edges, run_jobs):
    """Giving up is about the film too, so the other lines do not come back tomorrow."""
    lost = Row("A Film Since Deleted", 2003, rating=2.0)
    await flows.upload_export(owner, export.export(ratings=(lost,), watched=(lost,), diary=(lost,)))
    await run_jobs()
    (row,) = (await flows.unmatched(owner))["rows"]

    await flows.dismiss_row(owner, row["id"])
    assert (await flows.unmatched(owner))["rows"] == []
    assert (await flows.import_state(owner))["unmatched"] == 0
