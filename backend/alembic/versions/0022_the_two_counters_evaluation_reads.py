"""The two counters the named indicator set is denominated in.

Rotation rate and the accept and dismissal rates are counted per opportunity, and neither
opportunity left a countable trace. A staleness rotation writes a re-entry mark that the
next one overwrites, that a displacement and a not-now write identically, and that is
cleared outright when the film comes back. A restock stamps the profile version it ran
for, which answers whether another is due and keeps no history at all.

So both are counted where they happen. They are read by the operator's SQL and by nothing
else: evaluation reads but never feeds (ADR 0012).

Revision ID: 0022
Revises: 0021
Create Date: 2026-09-07

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | Sequence[str] | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Zero for every existing row, and honestly so: the rotations and restocks that
    # already happened are gone, and backfilling a guess would be worse than a count that
    # starts today. Every indicator is a rate, so it reads correctly from here on.
    op.add_column(
        "tier_states",
        sa.Column("staleness_rotations", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "feed_states",
        sa.Column("restock_counter", sa.Integer(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("feed_states", "restock_counter")
    op.drop_column("tier_states", "staleness_rotations")
