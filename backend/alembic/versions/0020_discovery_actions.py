"""Discovery's actions and the feed's economy: the visit clock, rotation, and provenance.

Revision ID: 0020
Revises: 0019
Create Date: 2026-09-07

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0020"
down_revision: str | Sequence[str] | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WATCH_ORIGIN = postgresql.ENUM(
    "discovery_accept", "hand_added", "import_seeded", name="watch_origin", create_type=False
)


def upgrade() -> None:
    # Provenance for the watch stamp, and nothing else (evaluation.md). The type already
    # names all three origins - it was written with the watch event in 0004 - so only the
    # column is new. Every film already in an account got there by hand or by import, and
    # the import path stamps its own watches, so the default is the honest backfill.
    op.add_column(
        "account_films",
        sa.Column("origin", WATCH_ORIGIN, server_default="hand_added", nullable=False),
    )

    # The card's clock. Existing rows start at zero, which is where the counter starts
    # too, so a shelf built before this migration has survived nothing - true, and the
    # generous reading: nothing rotates out for having been there before it was counted.
    op.add_column(
        "suggestions",
        sa.Column("arrived_at_refresh", sa.Integer(), server_default="0", nullable=False),
    )

    op.add_column("feed_states", sa.Column("visited_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "feed_states", sa.Column("fresh_since", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "feed_states",
        sa.Column("refresh_counter", sa.Integer(), server_default="0", nullable=False),
    )

    op.create_table(
        "suggestion_cooldowns",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Uuid(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "film_id",
            sa.Integer(),
            sa.ForeignKey("films.tmdb_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("reentry_refresh", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "account_id", "film_id", name="uq_suggestion_cooldowns_account_id_film_id"
        ),
    )

    # The magnitude guard's watermark. Zero for every existing version, so the first
    # regeneration after this ships counts every dismissal an account already has -
    # which is right: none of them has ever been read.
    op.add_column(
        "prose_profile_versions",
        sa.Column("dismissals", sa.Integer(), server_default="0", nullable=False),
    )
    # Postgres allows ADD VALUE inside a transaction but forbids *using* the value in
    # the same one, which is why nothing below writes a trigger row: the enum grows here
    # and the first row carrying it is written by the running app afterwards.
    op.execute("ALTER TYPE prose_trigger ADD VALUE IF NOT EXISTS 'dismissals'")


def downgrade() -> None:
    # The added enum value is deliberately left in place: Postgres cannot drop a value
    # from an enum, and re-adding one on a re-upgrade is what IF NOT EXISTS is for.
    op.drop_column("prose_profile_versions", "dismissals")
    op.drop_table("suggestion_cooldowns")
    op.drop_column("feed_states", "refresh_counter")
    op.drop_column("feed_states", "fresh_since")
    op.drop_column("feed_states", "visited_at")
    op.drop_column("suggestions", "arrived_at_refresh")
    op.drop_column("account_films", "origin")
