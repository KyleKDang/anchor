"""Bands, anchors, and dividers: the band structure a rating derives from.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-01

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ANCHOR_STATUS = postgresql.ENUM("current", "intended", name="anchor_status", create_type=False)


def upgrade() -> None:
    ANCHOR_STATUS.create(op.get_bind(), checkfirst=True)

    # A band judgment answers with a band rather than a verdict, and a plain band pick
    # names one film rather than two, so both of those columns become optional and the
    # check keeps exactly one answer per row.
    op.add_column("comparison_log_entries", sa.Column("band", sa.Float(), nullable=True))
    op.alter_column("comparison_log_entries", "film_b_id", nullable=True)
    op.alter_column("comparison_log_entries", "verdict", nullable=True)
    op.execute("ALTER TYPE comparison_kind ADD VALUE IF NOT EXISTS 'band' AFTER 'sliver'")
    op.create_check_constraint(
        "ck_comparison_log_entries_one_answer",
        "comparison_log_entries",
        "(band IS NULL) <> (verdict IS NULL)",
    )

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
        # Deferred for the same reason the placement's slot reference is: the guard
        # wanted is "a divider always names a judgment that exists", but the
        # account-realm wipe deletes the log and the dividers in one transaction, in
        # whatever order the cascades fire.
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
        sa.UniqueConstraint("account_id", "upper_band", name="uq_dividers_account_id_upper_band"),
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
        sa.Column("status", ANCHOR_STATUS, nullable=False),
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
    # Partial, so an intended designation never contends with the anchor it aims to be.
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


def downgrade() -> None:
    op.drop_table("anchor_designations")
    op.drop_table("dividers")
    op.drop_constraint(
        "ck_comparison_log_entries_one_answer", "comparison_log_entries", type_="check"
    )
    op.execute("DELETE FROM comparison_log_entries WHERE band IS NOT NULL")
    op.alter_column("comparison_log_entries", "verdict", nullable=False)
    op.alter_column("comparison_log_entries", "film_b_id", nullable=False)
    op.drop_column("comparison_log_entries", "band")
    ANCHOR_STATUS.drop(op.get_bind(), checkfirst=True)
    # The 'band' value stays in comparison_kind: PostgreSQL cannot drop one enum label,
    # and the rows that could have used it are gone.
