"""The ordering becomes ten band rows; the settling apparatus goes.

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-06

The direct-ordering redesign (ADR 0013). Every rated film of an old-shape account is
carried over to a band and a rank, dropping nothing, and everything that existed to
settle a position - tie groups, dividers, trust, provenance, drift, replacement requests,
the log's status column - goes with the design that needed it.

The carry-over reads three things in order, so that a film is only ever guessed at when
the account genuinely never said:

1. *The band the old dividers decide.* That is the rating the owner was being shown, so
   it is the rating they think this film has, and it must not change under them.
2. *The last synced rating.* An imported film's own Letterboxd value, which is the
   owner's judgment even where the divider structure never got tight enough to show it.
3. *Its neighbours.* Failing both, the film takes the band of the nearest film above it
   in the old sequence that does have one, then the nearest below, and finally 3.0 for
   an account with no band structure at all. This is the "dropping nothing" rule: a
   position-only film in the middle of the ordering is placed where the ordering already
   implied it sat, rather than being left unrated.

Rank comes from the old sequence: within a band, films keep the order their slots had,
and a tie group's members keep the order they were seated in. Nothing is reordered.

Two enums are renamed rather than merely trimmed, because CONTEXT.md now calls the act a
*re-rate*: a log entry's ``re_placement`` context and a watch event's ``re_placed``
outcome both become ``re_rated``/``re_rate``. The old drift-check context folds into the
same value, since a check on an existing rating is what a re-rate now is.

The downgrade restores the old shape but not the old data. Tie groups and dividers cannot
be reconstructed from band rows - that is the whole point of the change - so it recreates
the tables empty and leaves the placements it cannot re-seat.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017"
down_revision: str | Sequence[str] | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BANDS: tuple[float, ...] = (5.0, 4.5, 4.0, 3.5, 3.0, 2.5, 2.0, 1.5, 1.0, 0.5)
MIDDLE = 3.0
"""Where a film with no evidence at all lands: the middle of the scale, claiming least."""


def _existing(name: str) -> postgresql.ENUM:
    """A reference to a type this migration creates by hand, so DDL never re-creates it.

    Alembic emits ``CREATE TYPE`` for an enum it meets in a ``create_table``, which
    collides with the explicit creation the ordering of this migration needs. Naming the
    type without owning it is the way to have both.
    """
    return postgresql.ENUM(name=name, create_type=False)


DROPPED_TYPES = (
    "placement_trust",
    "placement_provenance",
    "anchor_status",
    "drift_stage",
    "drift_outcome",
    "comparison_status",
    "unlock_state",
)


def upgrade() -> None:
    bind = op.get_bind()

    carried = _read_orderings(bind)
    anchored = _read_anchors(bind)

    _widen_placements()
    _write_band_rows(bind, carried, anchored)
    _tighten_placements()

    _move_the_unlock_dots(bind)
    _retype_the_log()
    _rename_the_rewatch_outcome()
    _retype_the_warmup_marks()
    _reshape_the_counters(bind)
    _drop_the_settling_tables()

    for name in DROPPED_TYPES:
        op.execute(f"DROP TYPE IF EXISTS {name}")


# --- Reading the old shape ---


def _read_orderings(bind: sa.Connection) -> dict[str, list[tuple[str, float, int]]]:
    """Every account's rated films as (account_film_id, band, sequence), best first.

    One pass per account, because the band a film lands in depends on where its slot sat
    against that account's own dividers, and the neighbour fallback depends on what the
    films above and below it resolved to.
    """
    dividers: dict[str, dict[float, int]] = {}
    for account_id, upper_band, boundary in bind.execute(
        sa.text("SELECT account_id, upper_band, boundary FROM dividers")
    ):
        dividers.setdefault(str(account_id), {})[float(upper_band)] = int(boundary)

    seated: dict[str, list[tuple[int, str, float | None]]] = {}
    rows = bind.execute(
        sa.text(
            """
            SELECT s.account_id, s.position, p.account_film_id, af.last_synced_rating
            FROM tie_group_slots s
            JOIN placements p ON p.slot_id = s.id
            JOIN account_films af ON af.id = p.account_film_id
            ORDER BY s.account_id, s.position, p.placed_at, af.film_id
            """
        )
    )
    for account_id, position, account_film_id, synced in rows:
        seated.setdefault(str(account_id), []).append(
            (int(position), str(account_film_id), None if synced is None else float(synced))
        )

    return {
        account_id: _carry(members, dividers.get(account_id, {}))
        for account_id, members in seated.items()
    }


def _carry(
    members: list[tuple[int, str, float | None]], boundaries: dict[float, int]
) -> list[tuple[str, float, int]]:
    """One account's films, each given the band the three rules settle on.

    ``members`` arrives best-slot-first, so the sequence index is also the order the
    films keep inside whatever band they end up in.
    """
    decided: list[float | None] = []
    for position, _, synced in members:
        band = _band_of_slot(boundaries, position)
        if band is None and synced is not None and synced in BANDS:
            band = synced
        decided.append(band)

    filled = _fill_gaps(decided)
    return [
        (account_film_id, filled[index], index)
        for index, (_, account_film_id, _) in enumerate(members)
    ]


def _fill_gaps(decided: list[float | None]) -> list[float]:
    """Give every undecided film the band of its nearest decided neighbour, above first.

    Above first because the sequence runs best to worst: a film with nothing said about
    it sits below the last film that *was* decided, and taking that band claims the least
    while keeping the row monotonic. An account where nothing at all was decided falls to
    the middle band, which is the honest "we do not know" rather than a flattering guess.
    """
    filled: list[float] = []
    last: float | None = None
    for band in decided:
        if band is not None:
            last = band
        filled.append(band if band is not None else last)  # type: ignore[arg-type]

    following: float | None = None
    for index in range(len(filled) - 1, -1, -1):
        if filled[index] is None:
            filled[index] = following if following is not None else MIDDLE
        else:
            following = filled[index]
    return filled


def _band_of_slot(boundaries: dict[float, int], index: int) -> float | None:
    """The old derivation, inlined: a slot's band, or None while its dividers are unpinned.

    Copied rather than imported. A migration has to keep saying what it said on the day
    it was written, and the module this came from is deleted by the same commit.
    """
    possible = []
    for band in BANDS:
        rank = BANDS.index(band)
        over = [boundaries[key] for key in BANDS[:rank] if key in boundaries]
        under = [boundaries[key] for key in BANDS[rank:-1] if key in boundaries]
        if over and max(over) > index:
            continue
        if under and min(under) <= index:
            continue
        possible.append(band)
    return possible[0] if len(possible) == 1 else None


def _read_anchors(bind: sa.Connection) -> dict[str, object]:
    """The current anchor designations, as the mark they become on the placement."""
    rows = bind.execute(
        sa.text(
            "SELECT account_film_id, designated_at FROM anchor_designations "
            "WHERE status = 'current'"
        )
    )
    return {str(account_film_id): designated_at for account_film_id, designated_at in rows}


# --- Writing the new shape ---


def _widen_placements() -> None:
    """Add the new columns nullable, so the backfill has somewhere to write."""
    op.add_column("placements", sa.Column("band", sa.Float(), nullable=True))
    op.add_column("placements", sa.Column("rank", sa.Integer(), nullable=True))
    op.add_column("placements", sa.Column("anchored_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("placements", sa.Column("moved_at", sa.DateTime(timezone=True), nullable=True))


def _write_band_rows(
    bind: sa.Connection,
    carried: dict[str, list[tuple[str, float, int]]],
    anchored: dict[str, object],
) -> None:
    """Seat every carried film, and carry its anchor mark with it where it had one."""
    update = sa.text(
        "UPDATE placements SET band = :band, rank = :rank, anchored_at = :anchored_at "
        "WHERE account_film_id = :account_film_id"
    )
    for films in carried.values():
        ranks: dict[float, int] = {}
        for account_film_id, band, _ in sorted(films, key=lambda one: one[2]):
            ranks[band] = ranks.get(band, 0) + 1
            bind.execute(
                update,
                {
                    "band": band,
                    "rank": ranks[band],
                    # An anchor is always in the band it was marked in, and a carried mark
                    # is carried into whatever band the film landed in - which is the same
                    # band the owner was shown when they marked it.
                    "anchored_at": anchored.get(account_film_id),
                    "account_film_id": account_film_id,
                },
            )


def _tighten_placements() -> None:
    """Drop what the old shape needed, and make the new columns say what they mean."""
    op.drop_constraint("placements_slot_id_fkey", "placements", type_="foreignkey")
    op.drop_index("ix_placements_slot_id", table_name="placements")
    op.drop_column("placements", "slot_id")
    op.drop_column("placements", "trust")
    op.drop_column("placements", "provenance")

    op.alter_column("placements", "band", nullable=False)
    op.alter_column("placements", "rank", nullable=False)
    op.create_check_constraint(
        "ck_placements_band",
        "placements",
        "band IN (0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0)",
    )
    op.create_check_constraint("ck_placements_rank", "placements", "rank >= 1")
    op.create_unique_constraint(
        "uq_placements_account_id_band_rank",
        "placements",
        ["account_id", "band", "rank"],
        deferrable=True,
        initially="DEFERRED",
    )


def _move_the_unlock_dots(bind: sa.Connection) -> None:
    """The tier's one dot becomes two rows: absence is locked, and a row is the event.

    An account past the ready bar is past the forming bar too, so it earns the Discovery
    dot at the same state its Watchlist dot was in - a pending one is a dot it has not
    seen, and a seen one is a dot it has. An account still locked gets no rows and is
    armed by the next read, which is correct: under the new bars it may have just
    unlocked discovery, and it should hear about it.
    """
    op.execute("CREATE TYPE unlock AS ENUM ('discovery', 'watchlist')")
    op.create_table(
        "unlock_marks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Uuid(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("unlock", _existing("unlock"), nullable=False),
        sa.Column(
            "armed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("account_id", "unlock"),
    )
    op.create_index("ix_unlock_marks_account_id", "unlock_marks", ["account_id"])

    op.execute(
        """
        INSERT INTO unlock_marks (id, account_id, unlock, armed_at, seen_at)
        SELECT gen_random_uuid(), t.account_id, u.unlock, now(),
               CASE WHEN t.unlock_state = 'seen' THEN now() ELSE NULL END
        FROM tier_states t
        CROSS JOIN (SELECT unnest(ARRAY['discovery', 'watchlist']::unlock[]) AS unlock) u
        WHERE t.unlock_state <> 'locked'
        """
    )
    op.drop_column("tier_states", "unlock_state")


def _retype_the_log() -> None:
    """Three kinds where there were four, five contexts where there were seven, no status.

    Two old kinds fold into one: a sliver answer and a plain band answer both assert the
    band a film is in, which is what a band pick is. The two dropped contexts fold the
    same way - keep-comparing was the owner still rating the film they had just rated,
    and a drift check was a question about a rating that already existed.
    """
    op.drop_column("comparison_log_entries", "status")

    # The criteria check compares ``kind`` against a literal of the *old* type, so it has
    # to come off before the column is retyped and go back on after: left in place it
    # would be asking Postgres to compare the new enum with the old one.
    op.drop_constraint(
        "ck_comparison_log_entries_criteria_quality", "comparison_log_entries", type_="check"
    )
    op.execute("ALTER TYPE comparison_kind RENAME TO comparison_kind_old")
    op.execute("CREATE TYPE comparison_kind AS ENUM ('band_comparison', 'band_pick', 'criteria')")
    op.execute(
        """
        ALTER TABLE comparison_log_entries
        ALTER COLUMN kind TYPE comparison_kind
        USING (CASE
            WHEN kind::text = 'overall' THEN 'band_comparison'
            WHEN kind::text IN ('sliver', 'band') THEN 'band_pick'
            ELSE 'criteria'
        END)::comparison_kind
        """
    )
    op.execute("DROP TYPE comparison_kind_old")
    op.create_check_constraint(
        "ck_comparison_log_entries_criteria_quality",
        "comparison_log_entries",
        "(quality_id IS NOT NULL) = (kind = 'criteria')",
    )

    op.execute("ALTER TYPE comparison_context RENAME TO comparison_context_old")
    op.execute(
        "CREATE TYPE comparison_context AS ENUM "
        "('placement', 're_rate', 'warmup', 'spontaneous', 'seed_import')"
    )
    op.execute(
        """
        ALTER TABLE comparison_log_entries
        ALTER COLUMN context TYPE comparison_context
        USING (CASE
            WHEN context::text = 'keep_comparing' THEN 'placement'
            WHEN context::text IN ('drift_check', 're_placement') THEN 're_rate'
            ELSE context::text
        END)::comparison_context
        """
    )
    op.execute("DROP TYPE comparison_context_old")


def _rename_the_rewatch_outcome() -> None:
    """A rewatch that changed the owner's mind is a re-rate, and says so (CONTEXT.md)."""
    op.execute("ALTER TYPE rewatch_outcome RENAME TO rewatch_outcome_old")
    op.execute("CREATE TYPE rewatch_outcome AS ENUM ('confirmed', 're_rated', 'skipped')")
    op.execute(
        """
        ALTER TABLE watch_events
        ALTER COLUMN rewatch_outcome TYPE rewatch_outcome
        USING (CASE
            WHEN rewatch_outcome::text = 're_placed' THEN 're_rated'
            ELSE rewatch_outcome::text
        END)::rewatch_outcome
        """
    )
    op.execute("DROP TYPE rewatch_outcome_old")


def _retype_the_warmup_marks() -> None:
    """The evidence phase becomes the fresh fill's rate-some-films phase."""
    op.execute("ALTER TYPE warmup_mark RENAME TO warmup_mark_old")
    op.execute(
        "CREATE TYPE warmup_mark AS ENUM ('entered', 'anchors', 'rating', 'backlog', 'dismissed')"
    )
    op.execute(
        """
        ALTER TABLE warmup_progress
        ALTER COLUMN mark TYPE warmup_mark
        USING (CASE WHEN mark::text = 'evidence' THEN 'rating' ELSE mark::text END)::warmup_mark
        """
    )
    op.execute("DROP TYPE warmup_mark_old")


def _reshape_the_counters(bind: sa.Connection) -> None:
    """The metrics row and the prose watermark lose the dimensions that are gone.

    The metrics row's answered-comparison count survives as the band-comparison count,
    which is what those rows became. The prose watermark's is re-read from the account as
    it now stands rather than converted: it used to count answered comparisons and now
    counts every judgment, so a converted value would read as a backlog of change and buy
    an immediate regeneration nobody asked for. Re-reading it means the next one triggers
    on change the owner actually makes after this migration.
    """
    op.alter_column("taste_metrics", "explicit_comparisons", new_column_name="band_comparisons")
    op.drop_column("taste_metrics", "settled_films")

    op.alter_column("prose_profile_versions", "explicit_comparisons", new_column_name="judgments")
    op.drop_column("prose_profile_versions", "drift_resolutions")
    bind.execute(
        sa.text(
            """
            UPDATE prose_profile_versions v
            SET judgments = (
                SELECT count(*) FROM comparison_log_entries c
                WHERE c.account_id = v.account_id
            )
            """
        )
    )


def _drop_the_settling_tables() -> None:
    """Everything that existed to settle a position, in reference order."""
    op.drop_table("drift_evidence")
    op.drop_table("drift_flags")
    op.drop_table("replacement_requests")
    op.drop_table("anchor_designations")
    op.drop_table("dividers")
    op.drop_table("tie_group_slots")


# --- Downgrade: the old shape, not the old data ---


def downgrade() -> None:
    bind = op.get_bind()

    _recreate_the_settling_tables()
    _restore_the_counters()
    _restore_the_log()
    _restore_the_rewatch_outcome()
    _restore_the_warmup_marks()
    _restore_the_unlock_dot(bind)
    _narrow_placements()


def _recreate_the_settling_tables() -> None:
    op.execute("CREATE TYPE placement_trust AS ENUM ('provisional', 'full')")
    op.execute(
        "CREATE TYPE placement_provenance AS ENUM ('import_seeded', 'early_bail', 'completed')"
    )
    op.execute("CREATE TYPE anchor_status AS ENUM ('current', 'intended')")
    op.execute("CREATE TYPE drift_stage AS ENUM ('quiet', 'surfaced')")
    op.execute(
        "CREATE TYPE drift_outcome AS ENUM ('re_placed', 'kept', 're_pointed', 'self_resolved')"
    )

    op.create_table(
        "tie_group_slots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Uuid(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "account_id",
            "position",
            name="uq_tie_group_slots_account_id_position",
            deferrable=True,
            initially="DEFERRED",
        ),
    )
    op.create_index("ix_tie_group_slots_account_id", "tie_group_slots", ["account_id"])

    op.create_table(
        "dividers",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Uuid(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("upper_band", sa.Float(), nullable=False),
        sa.Column("boundary", sa.Integer(), nullable=False),
        sa.Column(
            "pinned_by_id",
            sa.Uuid(),
            sa.ForeignKey(
                "comparison_log_entries.id",
                ondelete="NO ACTION",
                deferrable=True,
                initially="DEFERRED",
            ),
            nullable=False,
        ),
        sa.Column(
            "moved_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("account_id", "upper_band"),
    )
    op.create_index("ix_dividers_account_id", "dividers", ["account_id"])

    op.create_table(
        "anchor_designations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Uuid(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("band", sa.Float(), nullable=False),
        sa.Column(
            "account_film_id",
            sa.Uuid(),
            sa.ForeignKey("account_films.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", _existing("anchor_status"), nullable=False),
        sa.Column(
            "designated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_anchor_designations_account_id", "anchor_designations", ["account_id"])
    op.create_index(
        "ix_anchor_designations_account_film_id", "anchor_designations", ["account_film_id"]
    )
    op.create_index(
        "uq_anchor_designations_current_band",
        "anchor_designations",
        ["account_id", "band"],
        unique=True,
        postgresql_where=sa.text("status = 'current'"),
    )
    op.create_index(
        "uq_anchor_designations_current_film",
        "anchor_designations",
        ["account_id", "account_film_id"],
        unique=True,
        postgresql_where=sa.text("status = 'current'"),
    )
    op.create_index(
        "uq_anchor_designations_intended",
        "anchor_designations",
        ["account_id"],
        unique=True,
        postgresql_where=sa.text("status = 'intended'"),
    )

    op.create_table(
        "replacement_requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Uuid(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "account_film_id",
            sa.Uuid(),
            sa.ForeignKey("account_films.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_replacement_requests_account_id", "replacement_requests", ["account_id"])
    op.create_index(
        "ix_replacement_requests_account_film_id", "replacement_requests", ["account_film_id"]
    )

    op.create_table(
        "drift_flags",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Uuid(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "account_film_id",
            sa.Uuid(),
            sa.ForeignKey("account_films.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("stage", _existing("drift_stage"), nullable=False),
        sa.Column("re_placing_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "opened_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", _existing("drift_outcome"), nullable=True),
    )
    op.create_index("ix_drift_flags_account_id", "drift_flags", ["account_id"])
    op.create_index("ix_drift_flags_account_film_id", "drift_flags", ["account_film_id"])
    op.create_index(
        "uq_drift_flags_open_film",
        "drift_flags",
        ["account_id", "account_film_id"],
        unique=True,
        postgresql_where=sa.text("closed_at IS NULL"),
    )

    op.create_table(
        "drift_evidence",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Uuid(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "flag_id",
            sa.Uuid(),
            sa.ForeignKey("drift_flags.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "entry_id",
            sa.Uuid(),
            sa.ForeignKey("comparison_log_entries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "attached_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("entry_id", name="uq_drift_evidence_entry_id"),
    )
    op.create_index("ix_drift_evidence_account_id", "drift_evidence", ["account_id"])
    op.create_index("ix_drift_evidence_flag_id", "drift_evidence", ["flag_id"])


def _restore_the_counters() -> None:
    op.alter_column("taste_metrics", "band_comparisons", new_column_name="explicit_comparisons")
    op.add_column(
        "taste_metrics",
        sa.Column("settled_films", sa.Integer(), nullable=False, server_default="0"),
    )
    op.alter_column("prose_profile_versions", "judgments", new_column_name="explicit_comparisons")
    op.add_column(
        "prose_profile_versions",
        sa.Column("drift_resolutions", sa.Integer(), nullable=False, server_default="0"),
    )


def _restore_the_log() -> None:
    op.execute("CREATE TYPE comparison_status AS ENUM ('active', 'in_tension', 'superseded')")
    op.add_column(
        "comparison_log_entries",
        sa.Column(
            "status", _existing("comparison_status"), nullable=False, server_default="active"
        ),
    )

    op.drop_constraint(
        "ck_comparison_log_entries_criteria_quality", "comparison_log_entries", type_="check"
    )
    op.execute("ALTER TYPE comparison_kind RENAME TO comparison_kind_new")
    op.execute("CREATE TYPE comparison_kind AS ENUM ('overall', 'sliver', 'band', 'criteria')")
    op.execute(
        """
        ALTER TABLE comparison_log_entries
        ALTER COLUMN kind TYPE comparison_kind
        USING (CASE
            WHEN kind::text = 'band_comparison' THEN 'overall'
            WHEN kind::text = 'band_pick' THEN 'band'
            ELSE 'criteria'
        END)::comparison_kind
        """
    )
    op.execute("DROP TYPE comparison_kind_new")
    op.create_check_constraint(
        "ck_comparison_log_entries_criteria_quality",
        "comparison_log_entries",
        "(quality_id IS NOT NULL) = (kind = 'criteria')",
    )

    op.execute("ALTER TYPE comparison_context RENAME TO comparison_context_new")
    op.execute(
        "CREATE TYPE comparison_context AS ENUM "
        "('placement', 're_placement', 'keep_comparing', 'drift_check', 'warmup', "
        "'spontaneous', 'seed_import')"
    )
    op.execute(
        """
        ALTER TABLE comparison_log_entries
        ALTER COLUMN context TYPE comparison_context
        USING (CASE
            WHEN context::text = 're_rate' THEN 're_placement'
            ELSE context::text
        END)::comparison_context
        """
    )
    op.execute("DROP TYPE comparison_context_new")


def _restore_the_rewatch_outcome() -> None:
    op.execute("ALTER TYPE rewatch_outcome RENAME TO rewatch_outcome_new")
    op.execute("CREATE TYPE rewatch_outcome AS ENUM ('confirmed', 're_placed', 'skipped')")
    op.execute(
        """
        ALTER TABLE watch_events
        ALTER COLUMN rewatch_outcome TYPE rewatch_outcome
        USING (CASE
            WHEN rewatch_outcome::text = 're_rated' THEN 're_placed'
            ELSE rewatch_outcome::text
        END)::rewatch_outcome
        """
    )
    op.execute("DROP TYPE rewatch_outcome_new")


def _restore_the_warmup_marks() -> None:
    op.execute("ALTER TYPE warmup_mark RENAME TO warmup_mark_new")
    op.execute(
        "CREATE TYPE warmup_mark AS ENUM ('entered', 'anchors', 'evidence', 'backlog', 'dismissed')"
    )
    op.execute(
        """
        ALTER TABLE warmup_progress
        ALTER COLUMN mark TYPE warmup_mark
        USING (CASE WHEN mark::text = 'rating' THEN 'evidence' ELSE mark::text END)::warmup_mark
        """
    )
    op.execute("DROP TYPE warmup_mark_new")


def _restore_the_unlock_dot(bind: sa.Connection) -> None:
    op.execute("CREATE TYPE unlock_state AS ENUM ('locked', 'pending', 'seen')")
    op.add_column(
        "tier_states",
        sa.Column(
            "unlock_state", _existing("unlock_state"), nullable=False, server_default="locked"
        ),
    )
    op.execute(
        """
        UPDATE tier_states t
        SET unlock_state = CASE WHEN m.seen_at IS NULL THEN 'pending' ELSE 'seen' END::unlock_state
        FROM unlock_marks m
        WHERE m.account_id = t.account_id AND m.unlock = 'watchlist'
        """
    )
    op.drop_table("unlock_marks")
    op.execute("DROP TYPE unlock")


def _narrow_placements() -> None:
    """Put the old columns back. The slot they should point at no longer exists.

    Every placement is deleted rather than left pointing nowhere: the old shape's whole
    invariant is that a placement's film is exactly one slot's member, and there is no
    honest way to rebuild the slots from band rows. A downgrade is a rollback of the
    code, not a recovery of the library.
    """
    op.execute("DELETE FROM placements")

    op.drop_constraint("uq_placements_account_id_band_rank", "placements", type_="unique")
    op.drop_constraint("ck_placements_band", "placements", type_="check")
    op.drop_constraint("ck_placements_rank", "placements", type_="check")
    op.drop_column("placements", "moved_at")
    op.drop_column("placements", "anchored_at")
    op.drop_column("placements", "rank")
    op.drop_column("placements", "band")

    op.add_column(
        "placements",
        sa.Column(
            "slot_id",
            sa.Uuid(),
            sa.ForeignKey(
                "tie_group_slots.id",
                ondelete="NO ACTION",
                deferrable=True,
                initially="DEFERRED",
            ),
            nullable=False,
        ),
    )
    op.create_index("ix_placements_slot_id", "placements", ["slot_id"])
    op.add_column(
        "placements",
        sa.Column("trust", _existing("placement_trust"), nullable=False),
    )
    op.add_column(
        "placements",
        sa.Column("provenance", _existing("placement_provenance"), nullable=False),
    )
