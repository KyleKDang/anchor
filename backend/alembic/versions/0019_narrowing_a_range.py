"""Narrowing a range: what a comparison was narrowing, and the boundary question's pair.

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-06

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | Sequence[str] | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable, and every existing row keeps them empty: the log before this migration
    # holds outright picks and seed-import picks, none of which narrowed anything. An
    # empty range is the true statement about them rather than a gap to be backfilled.
    op.add_column("comparison_log_entries", sa.Column("range_top", sa.Float(), nullable=True))
    op.add_column("comparison_log_entries", sa.Column("range_bottom", sa.Float(), nullable=True))
    op.add_column(
        "comparison_log_entries",
        sa.Column(
            "exemplar_upper_id",
            sa.Integer(),
            sa.ForeignKey("films.tmdb_id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    op.add_column(
        "comparison_log_entries",
        sa.Column(
            "exemplar_lower_id",
            sa.Integer(),
            sa.ForeignKey("films.tmdb_id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_comparison_log_entries_range_ends",
        "comparison_log_entries",
        "(range_top IS NULL) = (range_bottom IS NULL)",
    )
    op.create_check_constraint(
        "ck_comparison_log_entries_exemplars",
        "comparison_log_entries",
        "(exemplar_upper_id IS NULL) = (exemplar_lower_id IS NULL)"
        " AND (exemplar_upper_id IS NULL OR range_top IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_comparison_log_entries_exemplars", "comparison_log_entries")
    op.drop_constraint("ck_comparison_log_entries_range_ends", "comparison_log_entries")
    op.drop_column("comparison_log_entries", "exemplar_lower_id")
    op.drop_column("comparison_log_entries", "exemplar_upper_id")
    op.drop_column("comparison_log_entries", "range_bottom")
    op.drop_column("comparison_log_entries", "range_top")
