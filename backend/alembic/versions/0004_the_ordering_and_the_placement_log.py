"""The rating core: the ordering, placements, the comparison log, and watch events.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-01

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _enum(name: str, *values: str) -> postgresql.ENUM:
    # create_type=False throughout: each type is created once, explicitly, so that
    # ``create_table`` does not emit a second unconditional CREATE TYPE and collide.
    return postgresql.ENUM(*values, name=name, create_type=False)


PLACEMENT_TRUST = _enum("placement_trust", "provisional", "full")
PLACEMENT_PROVENANCE = _enum("placement_provenance", "import_seeded", "early_bail", "completed")
COMPARISON_KIND = _enum("comparison_kind", "overall", "sliver", "criteria")
COMPARISON_VERDICT = _enum("comparison_verdict", "a", "b", "tied", "skip")
COMPARISON_CONTEXT = _enum(
    "comparison_context",
    "placement",
    "re_placement",
    "keep_comparing",
    "drift_check",
    "warmup",
    "spontaneous",
)
COMPARISON_STATUS = _enum("comparison_status", "active", "in_tension", "superseded")
WATCH_STANDING = _enum("watch_standing", "up_next", "pool", "pinned", "plain_backlog")
WATCH_ORIGIN = _enum("watch_origin", "discovery_accept", "hand_added", "import_seeded")

TYPES = (
    PLACEMENT_TRUST,
    PLACEMENT_PROVENANCE,
    COMPARISON_KIND,
    COMPARISON_VERDICT,
    COMPARISON_CONTEXT,
    COMPARISON_STATUS,
    WATCH_STANDING,
    WATCH_ORIGIN,
)


def _film_fk() -> sa.ForeignKey:
    """RESTRICT, as account_films already does: an account operation never reaches the catalog."""
    return sa.ForeignKey("films.tmdb_id", ondelete="RESTRICT")


def upgrade() -> None:
    for type_ in TYPES:
        type_.create(op.get_bind(), checkfirst=True)

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
        # Deferred, because inserting a slot shifts every slot below it down by one, and
        # a single UPDATE walks rows in no guaranteed order, so mid-statement the
        # positions legitimately collide. Checking at commit still refuses a broken order.
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
        "placements",
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
        # Deferred rather than RESTRICT: the guard wanted is "a slot is never dropped
        # out from under its members", but the account-realm wipe deletes slots and
        # placements in one transaction, in whatever order the cascades fire.
        # Checking at commit refuses the bug and still allows the wipe.
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
        sa.Column("trust", PLACEMENT_TRUST, nullable=False),
        sa.Column("provenance", PLACEMENT_PROVENANCE, nullable=False),
        sa.Column(
            "placed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("account_film_id", name="uq_placements_account_film_id"),
    )
    op.create_index("ix_placements_account_id", "placements", ["account_id"])
    op.create_index("ix_placements_slot_id", "placements", ["slot_id"])

    op.create_table(
        "comparison_log_entries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Uuid(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", COMPARISON_KIND, nullable=False),
        sa.Column("subject_film_id", sa.Integer(), _film_fk(), nullable=False),
        sa.Column("film_a_id", sa.Integer(), _film_fk(), nullable=False),
        sa.Column("film_b_id", sa.Integer(), _film_fk(), nullable=False),
        sa.Column("verdict", COMPARISON_VERDICT, nullable=False),
        sa.Column("context", COMPARISON_CONTEXT, nullable=False),
        sa.Column("status", COMPARISON_STATUS, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_comparison_log_entries_account_id", "comparison_log_entries", ["account_id"]
    )
    op.create_index(
        "ix_comparison_log_entries_subject_film_id", "comparison_log_entries", ["subject_film_id"]
    )

    op.create_table(
        "watch_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Uuid(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("film_id", sa.Integer(), _film_fk(), nullable=False),
        sa.Column(
            "watched_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("standing", WATCH_STANDING, nullable=False),
        sa.Column("origin", WATCH_ORIGIN, nullable=False),
    )
    op.create_index("ix_watch_events_account_id", "watch_events", ["account_id"])
    op.create_index("ix_watch_events_film_id", "watch_events", ["film_id"])


def downgrade() -> None:
    op.drop_table("watch_events")
    op.drop_table("comparison_log_entries")
    op.drop_table("placements")
    op.drop_table("tie_group_slots")
    for type_ in TYPES:
        type_.drop(op.get_bind(), checkfirst=True)
