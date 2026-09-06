"""The migration chain applies and reverses, and 0015 carries an old account over.

The chain test runs on an empty database, which proves the schema work. The carry-over
test builds an account in the *old* shape by hand - tie-group slots, dividers, an anchor
designation, a position-only film - runs the one migration over it, and reads back what
the owner would now see. Built by hand rather than through the old code, because the old
code is deleted by the same commit and a migration has to keep working against the shape
it was written for.
"""

import uuid

import psycopg
import pytest

from conftest import _admin, _url_for, migrate


def test_migrations_upgrade_downgrade_and_upgrade_again():
    name = f"anchor_test_migrations_{uuid.uuid4().hex[:8]}"
    _admin(f'CREATE DATABASE "{name}"')
    try:
        url = _url_for(name)
        migrate(url, "head")
        migrate(url, "base")
        migrate(url, "head")
    finally:
        _admin(f'DROP DATABASE "{name}" WITH (FORCE)')


# --- The carry-over ---

# One old-shape account, in the three shapes the ticket names. The ordering is six slots:
#
#   0  film 1  a 5.0 by the dividers, and the account's anchor
#   1  film 2  a 5.0 by the dividers
#   2  film 3  a 3.0 by the dividers
#   3  film 4  position-only, and last synced at 2.0 on Letterboxd
#   4  film 5  a seeded tie group of two, position-only, no synced rating
#   4  film 6
#
# The dividers say the 5.0/4.5 boundary is at slot 2 and the 3.0/2.5 boundary at slot 3,
# so slots 0-1 derive to 5.0, slot 2 derives to 3.0, and everything below derives to
# nothing at all.

OLD_ACCOUNT = """
INSERT INTO accounts (id, email, password_hash, verified_at)
VALUES (:account, 'old@example.com', 'x', now());

INSERT INTO films (tmdb_id, title, release_year, overview, genres, keywords, credits,
                   vote_average, vote_count)
SELECT n, 'Film ' || n, 2000, '', '{}', '{}', '{}'::jsonb, 7.0, 1000
FROM generate_series(1, 6) AS n;

INSERT INTO account_films (id, account_id, film_id, state, last_synced_rating)
VALUES
  (:af1, :account, 1, 'rated', NULL),
  (:af2, :account, 2, 'rated', NULL),
  (:af3, :account, 3, 'rated', NULL),
  (:af4, :account, 4, 'rated', 2.0),
  (:af5, :account, 5, 'rated', NULL),
  (:af6, :account, 6, 'rated', NULL);

INSERT INTO tie_group_slots (id, account_id, position)
VALUES
  (:s0, :account, 0), (:s1, :account, 1), (:s2, :account, 2),
  (:s3, :account, 3), (:s4, :account, 4);

INSERT INTO placements (id, account_id, account_film_id, slot_id, trust, provenance, placed_at)
VALUES
  (gen_random_uuid(), :account, :af1, :s0, 'full', 'completed', now()),
  (gen_random_uuid(), :account, :af2, :s1, 'full', 'completed', now()),
  (gen_random_uuid(), :account, :af3, :s2, 'full', 'completed', now()),
  (gen_random_uuid(), :account, :af4, :s3, 'full', 'completed', now()),
  (gen_random_uuid(), :account, :af5, :s4, 'provisional', 'import_seeded', now()),
  (gen_random_uuid(), :account, :af6, :s4, 'provisional', 'import_seeded',
   now() + interval '1 second');

INSERT INTO comparison_log_entries
  (id, account_id, kind, subject_film_id, film_a_id, film_b_id, verdict, band, context, status)
VALUES
  (:pin, :account, 'band', 1, 1, NULL, NULL, 5.0, 'placement', 'active'),
  (gen_random_uuid(), :account, 'overall', 2, 2, 1, 'b', NULL, 'placement', 'superseded'),
  (gen_random_uuid(), :account, 'sliver', 3, 3, 1, NULL, 3.0, 'keep_comparing', 'active'),
  (gen_random_uuid(), :account, 'overall', 4, 4, 3, 'a', NULL, 'drift_check', 'in_tension');

INSERT INTO dividers (id, account_id, upper_band, boundary, pinned_by_id)
VALUES
  (gen_random_uuid(), :account, 5.0, 2, :pin),
  (gen_random_uuid(), :account, 4.5, 2, :pin),
  (gen_random_uuid(), :account, 4.0, 2, :pin),
  (gen_random_uuid(), :account, 3.5, 2, :pin),
  (gen_random_uuid(), :account, 3.0, 3, :pin);

INSERT INTO anchor_designations (id, account_id, band, account_film_id, status)
VALUES (gen_random_uuid(), :account, 5.0, :af1, 'current');

INSERT INTO tier_states (id, account_id, unlock_state)
VALUES (gen_random_uuid(), :account, 'pending');
"""

IDS = ("account", "af1", "af2", "af3", "af4", "af5", "af6", "s0", "s1", "s2", "s3", "s4", "pin")


@pytest.fixture
def old_shape_account():
    """A database at revision 0014 holding one account built the old way."""
    name = f"anchor_test_carry_{uuid.uuid4().hex[:8]}"
    _admin(f'CREATE DATABASE "{name}"')
    url = _url_for(name)
    migrate(url, "0014")
    ids = {key: uuid.uuid4() for key in IDS}
    with psycopg.connect(url, autocommit=True) as conn:
        for statement in OLD_ACCOUNT.strip().split(";\n"):
            if statement.strip():
                conn.execute(*_bound(statement, ids))
    try:
        yield url, ids
    finally:
        _admin(f'DROP DATABASE "{name}" WITH (FORCE)')


def _bound(statement, ids):
    """psycopg takes %(name)s placeholders; the SQL above reads better with :name."""
    for key in ids:
        statement = statement.replace(f":{key}", f"%({key})s")
    return statement, {key: str(value) for key, value in ids.items()}


def rows(url, query):
    with psycopg.connect(url, autocommit=True) as conn:
        return conn.execute(query).fetchall()


def test_the_carry_over_drops_no_rated_film(old_shape_account):
    url, _ = old_shape_account
    migrate(url, "0015")

    carried = rows(
        url,
        """
        SELECT af.film_id, p.band, p.rank FROM placements p
        JOIN account_films af ON af.id = p.account_film_id
        ORDER BY p.band DESC, p.rank
        """,
    )
    assert [film_id for film_id, _, _ in carried] == [1, 2, 3, 4, 5, 6]


def test_a_derivable_band_carries_the_band_the_owner_was_shown(old_shape_account):
    """That is the rating they think the film has; it must not change under them."""
    url, _ = old_shape_account
    migrate(url, "0015")

    bands = dict(
        rows(
            url,
            "SELECT af.film_id, p.band FROM placements p "
            "JOIN account_films af ON af.id = p.account_film_id",
        )
    )
    assert bands[1] == 5.0
    assert bands[2] == 5.0
    assert bands[3] == 3.0


def test_a_position_only_film_falls_back_to_its_last_synced_rating(old_shape_account):
    url, _ = old_shape_account
    migrate(url, "0015")

    bands = dict(
        rows(
            url,
            "SELECT af.film_id, p.band FROM placements p "
            "JOIN account_films af ON af.id = p.account_film_id",
        )
    )
    assert bands[4] == 2.0, "Letterboxd's own value is the owner's judgment"


def test_a_seeded_tie_group_with_nothing_said_takes_its_neighbours_band(old_shape_account):
    """The dropping-nothing rule: the ordering already implied where these sat."""
    url, _ = old_shape_account
    migrate(url, "0015")

    bands = dict(
        rows(
            url,
            "SELECT af.film_id, p.band FROM placements p "
            "JOIN account_films af ON af.id = p.account_film_id",
        )
    )
    assert bands[5] == bands[6] == 2.0


def test_ranks_come_from_the_old_sequence_and_are_dense(old_shape_account):
    url, _ = old_shape_account
    migrate(url, "0015")

    carried = rows(
        url,
        """
        SELECT p.band, p.rank, af.film_id FROM placements p
        JOIN account_films af ON af.id = p.account_film_id
        ORDER BY p.band DESC, p.rank
        """,
    )
    per_band = {}
    for band, rank, film_id in carried:
        per_band.setdefault(band, []).append((rank, film_id))
    for band, seats in per_band.items():
        assert [rank for rank, _ in seats] == list(range(1, len(seats) + 1)), band
    assert per_band[5.0] == [(1, 1), (2, 2)], "the old slot order is kept"
    assert per_band[2.0] == [(1, 4), (2, 5), (3, 6)], "and a tie group keeps its seating"


def test_the_anchor_designation_becomes_a_mark_on_the_placement(old_shape_account):
    url, _ = old_shape_account
    migrate(url, "0015")

    marked = rows(
        url,
        """
        SELECT af.film_id, p.band FROM placements p
        JOIN account_films af ON af.id = p.account_film_id
        WHERE p.anchored_at IS NOT NULL
        """,
    )
    assert marked == [(1, 5.0)]


def test_the_log_is_re_typed_and_loses_its_status(old_shape_account):
    """Overall becomes a band comparison; sliver and band both become band picks."""
    url, _ = old_shape_account
    migrate(url, "0015")

    kinds = rows(
        url,
        "SELECT subject_film_id, kind::text, context::text "
        "FROM comparison_log_entries ORDER BY subject_film_id",
    )
    assert kinds == [
        (1, "band_pick", "placement"),
        (2, "band_comparison", "placement"),
        (3, "band_pick", "placement"),
        (4, "band_comparison", "re_rate"),
    ]
    columns = rows(
        url,
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'comparison_log_entries' AND column_name = 'status'",
    )
    assert columns == []


def test_the_settling_tables_are_gone(old_shape_account):
    url, _ = old_shape_account
    migrate(url, "0015")

    left = rows(
        url,
        """
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = current_schema()
          AND table_name IN ('tie_group_slots', 'dividers', 'anchor_designations',
                             'drift_flags', 'drift_evidence', 'replacement_requests')
        """,
    )
    assert left == []


def test_the_watchlist_dot_carries_over_and_discovery_earns_one_too(old_shape_account):
    """An account past ready is past forming, so it has unlocked discovery as well."""
    url, _ = old_shape_account
    migrate(url, "0015")

    dots = rows(url, "SELECT unlock::text, seen_at IS NULL FROM unlock_marks ORDER BY unlock")
    assert dots == [("discovery", True), ("watchlist", True)]
