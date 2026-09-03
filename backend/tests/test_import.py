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
from export import Row
from faketmdb import FilmFixture
from flows import account_id
from invariants import (
    assert_bands_derived,
    assert_bands_well_formed,
    assert_ordering_well_formed,
    dividers,
    last_synced_ratings,
    placement_trust,
    seeded_slots,
    watch_clock,
    watch_events,
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
    assert (state["review_pending"], state["unmatched"]) == (0, 0)

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
