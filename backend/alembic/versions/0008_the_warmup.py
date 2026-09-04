"""The warmup: the one thing about onboarding that is not derived.

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

WARMUP_MARK = postgresql.ENUM(
    "entered",
    "anchors",
    "evidence",
    "backlog",
    "dismissed",
    name="warmup_mark",
    create_type=False,
)


def upgrade() -> None:
    WARMUP_MARK.create(op.get_bind(), checkfirst=True)

    # Skips and dismissals only: everything else the warmup shows is derived from the
    # anchors, the comparison log, and the backlog, and a stored copy could disagree.
    op.create_table(
        "warmup_progress",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Uuid(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("mark", WARMUP_MARK, nullable=False),
        sa.Column("band", sa.Float(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_warmup_progress_account_id", "warmup_progress", ["account_id"])
    # NULLs are distinct in Postgres, so one unique constraint over both columns would
    # let a phase-level mark be written twice. Two partial indexes say what is meant.
    op.create_index(
        "uq_warmup_progress_phase",
        "warmup_progress",
        ["account_id", "mark"],
        unique=True,
        postgresql_where=sa.text("band IS NULL"),
    )
    op.create_index(
        "uq_warmup_progress_band",
        "warmup_progress",
        ["account_id", "mark", "band"],
        unique=True,
        postgresql_where=sa.text("band IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_table("warmup_progress")
    WARMUP_MARK.drop(op.get_bind(), checkfirst=True)
