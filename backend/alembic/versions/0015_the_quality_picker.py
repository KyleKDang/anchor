"""The quality picker: what Anchor guessed, and whether the owner has answered yet.

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-06

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | Sequence[str] | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable and unbackfilled on both: null is the honest value for every account that
    # exists today. Nobody has answered the picker, and nothing has been guessed for
    # them, which is exactly what the picker will show them when they first open it.
    op.add_column(
        "accounts",
        sa.Column("qualities_picked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "quality_list_entries",
        sa.Column("suggested_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("quality_list_entries", "suggested_at")
    op.drop_column("accounts", "qualities_picked_at")
