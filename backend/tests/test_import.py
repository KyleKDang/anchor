"""The seed import, driven the way its owner drives it: upload, resolve, live with it.

Every test speaks the JSON API over a real database with TMDB faked at its HTTP edge,
per testing.md. The export fixtures carry the real 592-row export's awkward rows - a
non-breaking space after an en dash, a middle dot, commas inside quoted titles, accents
- alongside the synthetic ones no real export contained: a missing year, a TV-side row,
a deleted film, a duplicate title and year.
"""

import logging
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
    assert_ordering_well_formed,
    bands_reported,
    comparison_log,
    last_synced_ratings,
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
"""One rated film per band, so a full export fills all ten rows of the wall."""

TWIN = FilmFixture(7100, "Seed 04 Also", release_date="2010-01-01", popularity=12.0)
"""A second film at 3.0, so one band row holds two and the default order has to seat them."""

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


def _default_order(tmdb_id):
    """The default order's key over the fixtures here, restated rather than imported.

    Every fixture in this module shares a vote average and count, so the shrinkage is a
    constant and the title tiebreak is the whole of it - which is the honest way to say
    what the assertions expect without asking the code under test what it thinks.
    """
    return next(film.title for film in (*SEEDS, TWIN, SEEN_ONLY, WANTED) if film.tmdb_id == tmdb_id)


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
    owner meets it: every rated row lands in its band at its default rank, rated and
    final, watchlist rows seed the backlog, a watched row with no rating takes a
    rate-later seat, diary rows become watch events, and profile.csv yields a favourite
    and nothing else.
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
    # Ten band rows, best first, one per half-star value - and the eleventh rating lands
    # in the row its value names, seated by the default order rather than by arrival.
    assert flows.ordering_of(payload) == {
        **{band: [film.tmdb_id] for film, band in zip(SEEDS, BANDS, strict=True)},
        3.0: sorted([SEEDS[4].tmdb_id, TWIN.tmdb_id], key=_default_order),
    }
    assert flows.bands_of(payload) == {
        **{film.tmdb_id: band for film, band in zip(SEEDS, BANDS, strict=True)},
        TWIN.tmdb_id: 3.0,
    }
    await bands_reported(db, account, flows.bands_of(payload))
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

    # The sync list's baseline: what Letterboxd holds, as far as Anchor knows.
    assert await last_synced_ratings(db, account) == {
        **{film.tmdb_id: band for film, band in zip(SEEDS, BANDS, strict=True)},
        TWIN.tmdb_id: 3.0,
    }


async def test_two_films_of_one_band_take_the_default_order(owner, stocked, db, run_jobs):
    """Nothing about arrival is a judgment: the row is seated by the rule, not by the CSV.

    The two are exported in the order the assertion does *not* expect, so the only thing
    that could produce the row read back is the default order itself.
    """
    await flows.upload_export(
        owner,
        export.export(
            ratings=(
                Row(TWIN.title, TWIN.year, rating=3.0),
                Row(SEEDS[4].title, SEEDS[4].year, rating=3.0),
            )
        ),
    )
    await run_jobs()

    row = flows.ordering_of(await flows.rated(owner))[3.0]
    assert row == sorted([SEEDS[4].tmdb_id, TWIN.tmdb_id], key=_default_order)
    await assert_ordering_well_formed(db, await owner_id(owner))


async def test_a_low_vote_film_with_a_perfect_average_does_not_top_its_row(
    owner, stocked, tmdb, db, run_jobs
):
    """The shrinkage, at the one moment it matters most: a whole library seated at once."""
    obscure = FilmFixture(7600, "Obscure Gem", "1999-01-01", vote_average=10.0, vote_count=3)
    famous = FilmFixture(7601, "Everyone Saw It", "1999-01-01", vote_average=8.3, vote_count=12000)
    tmdb.with_films(obscure, famous)

    await flows.upload_export(
        owner,
        export.export(
            ratings=(
                Row(obscure.title, obscure.year, rating=4.0),
                Row(famous.title, famous.year, rating=4.0),
            )
        ),
    )
    await run_jobs()

    assert flows.ordering_of(await flows.rated(owner))[4.0] == [famous.tmdb_id, obscure.tmdb_id]


async def test_nothing_provisional_exists_and_the_sync_list_is_empty(owner, stocked, db, run_jobs):
    """A rated row is rated and final the moment it is matched (ADR 0013)."""
    await flows.upload_export(owner, export.export(ratings=_rated_rows()))
    await run_jobs()

    payload = await flows.rated(owner)
    listed = flows.listed_of(payload)
    assert listed and not any("provisional" in film for film in listed)
    assert "settling" not in payload
    assert (await flows.sync_list(owner))["count"] == 0


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
    assert flows.ordering_of(await flows.rated(owner)) == {}


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
    assert flows.ordering_of(await flows.rated(owner)) == {}


# --- The retrain is the import's, not the import ---


async def test_a_retrain_that_dies_leaves_the_finished_import_finished(
    owner, edges, db, run_jobs, monkeypatch, caplog
):
    """The taste profile is a derived artifact; the import's outcome never waits on it.

    A retrain that died after the last row had landed used to roll the completion back
    with it, and a ``matching`` import hides the review queue, the unmatched list and
    the re-import control - so the account read as mid-import for good, with every open
    row unreachable. The completion commits first and the retrain fails on its own:
    logged rather than failing the job, because the job's retry is for TMDB going down
    mid-row, and re-running a finished import only reaches the same failing call.
    """
    from anchor import taste

    seen: list[str] = []

    async def die(session, account_id):
        # What the record says when the retrain runs, read outside the job's own
        # transaction. The completion has to be committed by now: the retrain the
        # kernel kills raises nothing, and would take an uncommitted flip down with it.
        async with db.sessions() as fresh:
            seen.append((await fresh.execute(text("SELECT status FROM imports"))).scalar_one())
        raise RuntimeError("the trainer ran out of memory")

    monkeypatch.setattr(taste, "retrain", die)
    await flows.upload_export(
        owner,
        export.export(
            ratings=(
                Row("WALL·E", 2008, rating=4.5),  # auto-matched
                Row("Twin Title", 2001, rating=4.0),  # queued to review
                Row("A Film Since Deleted", 2003, rating=2.0),  # unmatched
            )
        ),
    )
    with caplog.at_level(logging.ERROR, logger="anchor.jobs"):
        await run_jobs()

    # Committed before the retrain ran - and the job ran it once, not once per retry.
    assert seen == ["complete"]

    state = await flows.import_state(owner)
    assert state["status"] == "complete"
    assert (state["pending"], state["review_pending"], state["unmatched"]) == (0, 1, 1)
    assert flows.bands_of(await flows.rated(owner)) == {WALL_E.tmdb_id: 4.5}
    assert [row["name"] for row in (await flows.review_queue(owner))["rows"]] == ["Twin Title"]
    assert [row["name"] for row in (await flows.unmatched(owner))["rows"]] == [
        "A Film Since Deleted"
    ]

    # Swallowed, not silenced: the failure is on the worker's log, traceback and all.
    [record] = [r for r in caplog.records if r.name == "anchor.jobs"]
    assert record.exc_info is not None
    assert isinstance(record.exc_info[1], RuntimeError)


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
    """Counted, not described: "3 ratings, 3 answers, 1 anchor" is something to weigh."""
    await flows.upload_export(
        owner,
        export.export(
            ratings=_rated_rows()[:3],
            watchlist=(Row(WANTED.title, WANTED.year),),
            diary=(Row(SEEDS[0].title, SEEDS[0].year),),
        ),
    )
    await run_jobs()
    await flows.mark_anchor(owner, SEEDS[0])

    warning = await flows.reset_warning(owner)
    assert (warning["rated_films"], warning["backlog_films"], warning["watch_events"]) == (3, 1, 1)
    assert warning["anchors"] == 1
    # Three imported ratings are three recorded band picks: what the log is about to lose.
    assert warning["judgments"] == 3


@pytest.mark.settings(import_reset_confirm_judgments=0)
async def test_a_re_import_over_a_real_log_makes_the_owner_type(owner, stocked, run_jobs):
    """Once the owner has made real judgments, the log is worth stopping for."""
    await flows.upload_export(owner, export.export(ratings=_rated_rows()[:3]))
    await run_jobs()
    await flows.rate(owner, SEEN_ONLY, 2.0)

    warning = await flows.reset_warning(owner)
    assert warning["judgments"] > 0
    assert warning["confirmation_required"]

    again = export.export(ratings=(Row(WANTED.title, WANTED.year, rating=1.0),))
    await flows.upload_export(owner, again, expect=409)
    await flows.upload_export(owner, again, confirm="not the phrase", expect=409)
    # Everything is still there: a refused reset destroys nothing.
    assert len(flows.listed_of(await flows.rated(owner))) == 4

    await flows.upload_export(owner, again, confirm=warning["confirmation_phrase"])


async def test_a_re_import_rebuilds_from_the_new_export_alone(owner, stocked, db, run_jobs):
    """No merge path, ever: everything the account held goes, seeded and organic alike."""
    account = await owner_id(owner)
    await flows.upload_export(owner, export.export(ratings=_rated_rows()[:3]))
    await run_jobs()
    await flows.mark_anchor(owner, SEEDS[0])
    await flows.add_to_backlog(owner, WANTED)  # hand-added, and it goes too

    await flows.upload_export(
        owner,
        export.export(
            ratings=(Row(SEEN_ONLY.title, SEEN_ONLY.year, rating=2.0),),
            watchlist=(Row(TWIN.title, TWIN.year),),
        ),
    )
    await run_jobs()

    assert flows.ordering_of(await flows.rated(owner)) == {2.0: [SEEN_ONLY.tmdb_id]}
    assert flows.bands_of(await flows.rated(owner)) == {SEEN_ONLY.tmdb_id: 2.0}
    assert [film["tmdb_id"] for film in (await flows.backlog(owner))["films"]] == [TWIN.tmdb_id]
    assert await anchor_rows(db, account) == {}

    # The one exception to the comparison log's never-deleted rule: only the new export's
    # own band judgments remain, and every one of them was made by this import.
    log = await comparison_log(db, account)
    assert {str(entry[7]) for entry in log} == {"seed_import"}
    assert len(log) == 1
    await assert_ordering_well_formed(db, account)

    # The owner is still logged in on the other side of it: a session is not account data.
    assert (await flows.import_state(owner))["counts"]["rating"] == 1


async def test_a_first_import_over_an_account_the_owner_already_started_resets_it(
    owner, stocked, db, run_jobs
):
    """The gate is what the account holds, not whether it has imported before.

    An owner who tries the app before their export is ready holds films no import ever
    put there. Gating the wipe on a prior import merged the export into them, which is
    the merge path onboarding-and-import.md forbids outright, so the wipe is now
    unconditional: every seed import rebuilds the account from its export alone.
    """
    account = await owner_id(owner)
    await flows.build_ordering(owner, [SEEDS[0], SEEDS[1]])
    await flows.add_to_backlog(owner, WANTED)

    # No import has ever run here, and there is still plenty to lose.
    assert (await flows.import_state(owner))["status"] == "none"
    warning = await flows.reset_warning(owner)
    assert (warning["rated_films"], warning["backlog_films"]) == (2, 1)

    await flows.upload_export(
        owner, export.export(ratings=(Row(SEEN_ONLY.title, SEEN_ONLY.year, rating=2.0),))
    )
    await run_jobs()

    assert flows.ordering_of(await flows.rated(owner)) == {2.0: [SEEN_ONLY.tmdb_id]}
    assert (await flows.backlog(owner))["films"] == []
    log = await comparison_log(db, account)
    assert {str(entry[7]) for entry in log} == {"seed_import"}
    await assert_ordering_well_formed(db, account)


async def test_a_first_import_into_an_empty_account_destroys_nothing(owner, stocked, run_jobs):
    """The unconditional wipe must stay invisible on the path it was never about.

    Onboarding's whole point is an account with nothing in it, so the warning enumerates
    nothing and the upload asks for no confirmation.
    """
    warning = await flows.reset_warning(owner)
    assert (warning["rated_films"], warning["judgments"], warning["backlog_films"]) == (0, 0, 0)
    assert not warning["confirmation_required"]

    await flows.upload_export(owner, export.export(ratings=_rated_rows()))
    await run_jobs()
    assert len(flows.listed_of(await flows.rated(owner))) == 11


@pytest.mark.settings(import_reset_confirm_judgments=0)
async def test_a_first_import_over_a_real_log_makes_the_owner_type(owner, stocked, run_jobs):
    """A log worth protecting is worth protecting whether or not an import made it."""
    await flows.build_ordering(owner, [SEEDS[0], SEEDS[1]], band=4.0)

    warning = await flows.reset_warning(owner)
    assert warning["judgments"] > 0
    assert warning["confirmation_required"]

    again = export.export(ratings=(Row(WANTED.title, WANTED.year, rating=1.0),))
    await flows.upload_export(owner, again, expect=409)
    await flows.upload_export(owner, again, confirm="not the phrase", expect=409)
    # A refused reset destroys nothing: both hand-placed films are still there.
    assert len(flows.listed_of(await flows.rated(owner))) == 2

    await flows.upload_export(owner, again, confirm=warning["confirmation_phrase"])


async def test_every_rated_film_records_what_letterboxd_holds(owner, stocked, db, run_jobs):
    """The sync list's baseline, which only the import is ever in a position to write.

    It used to be skipped for exactly the films the owner had rated before importing,
    because the seed-never-re-rates guard returned ahead of writing it. With the wipe
    unconditional those films no longer survive to be skipped, and the guard is left
    covering the case it was written for: one film named by two rows of one export.
    """
    await flows.build_ordering(owner, [SEEDS[0], SEEDS[1]], band=4.0)

    await flows.upload_export(
        owner,
        export.export(
            ratings=(
                *_rated_rows(),
                # The same film again, lower down: the second row must not overwrite it.
                Row(SEEDS[0].title, SEEDS[0].year, rating=1.0),
            )
        ),
    )
    await run_jobs()

    rated = await flows.rated(owner)
    assert await last_synced_ratings(db, await owner_id(owner)) == flows.bands_of(rated)


# --- The provisional lifecycle, as it applies to seeded films ---


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


# --- What an upload is not allowed to do ---


async def test_a_member_that_decompresses_to_a_flood_is_refused(owner):
    """A zip's claimed size is checked before it is read, not after.

    A few kilobytes of compressed repetition expands to gigabytes, and the row ceiling
    cannot help because it is only reachable once the member is already in memory.
    """
    await flows.upload_export(owner, _bloated(), expect=422)
    assert (await flows.import_state(owner))["status"] == "none"


def _bloated():
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("ratings.csv", "Date,Name,Year,Letterboxd URI,Rating\n" + "a" * 40_000_000)
    return buffer.getvalue()


async def test_a_row_can_only_ever_point_the_rescue_at_letterboxd(
    owner, edges, letterboxd, run_jobs
):
    """The URI comes out of an uploaded file, so it is a URL the owner chose for us.

    The rescue fetches it server-side, which makes anything but a Letterboxd link a
    request the owner gets to aim at the inside of the network. Only Letterboxd's own
    hosts survive parsing; anything else is kept as a row with no link to follow.
    """
    await flows.upload_export(
        owner,
        export.export(
            ratings=(
                Row("A Film Since Deleted", 2003, rating=2.0, uri="http://169.254.169.254/latest"),
            )
        ),
    )
    await run_jobs()

    (row,) = (await flows.unmatched(owner))["rows"]
    assert not row["rescuable"]
    await flows.rescue_row(owner, row["id"], expect=409)
    assert letterboxd.requests == []
