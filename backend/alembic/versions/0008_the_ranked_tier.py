"""The ranked tier: seats and overrides on backlog films, and what the last refresh saw.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-03

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TIER_ZONE = postgresql.ENUM("up_next", "pool", name="tier_zone", create_type=False)
UNLOCK_STATE = postgresql.ENUM("locked", "pending", "seen", name="unlock_state", create_type=False)


def upgrade() -> None:
    TIER_ZONE.create(op.get_bind(), checkfirst=True)
    UNLOCK_STATE.create(op.get_bind(), checkfirst=True)

    # The tier hangs off backlog account-films rather than living in a table of its own:
    # pin and veto apply whether or not the film holds a seat, and the cooldown marks
    # have to outlive the seat that earned them (data-model.md).
    op.add_column("account_films", sa.Column("tier_zone", TIER_ZONE, nullable=True))
    op.add_column("account_films", sa.Column("tier_position", sa.Integer(), nullable=True))
    op.add_column("account_films", sa.Column("tier_entered_watch", sa.Integer(), nullable=True))
    op.add_column("account_films", sa.Column("tier_reentry_watch", sa.Integer(), nullable=True))
    op.add_column(
        "account_films", sa.Column("pinned_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "account_films", sa.Column("vetoed_at", sa.DateTime(timezone=True), nullable=True)
    )

    op.create_table(
        "tier_states",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Uuid(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("refreshed_trained_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("refreshed_watch_clock", sa.Integer(), nullable=True),
        sa.Column("due", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("unlock_state", UNLOCK_STATE, server_default="locked", nullable=False),
        sa.UniqueConstraint("account_id", name="uq_tier_states_account_id"),
    )
    op.create_index("ix_tier_states_account_id", "tier_states", ["account_id"])


def downgrade() -> None:
    op.drop_table("tier_states")
    op.drop_column("account_films", "vetoed_at")
    op.drop_column("account_films", "pinned_at")
    op.drop_column("account_films", "tier_reentry_watch")
    op.drop_column("account_films", "tier_entered_watch")
    op.drop_column("account_films", "tier_position")
    op.drop_column("account_films", "tier_zone")
    UNLOCK_STATE.drop(op.get_bind(), checkfirst=True)
    TIER_ZONE.drop(op.get_bind(), checkfirst=True)
