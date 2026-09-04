"""Drift flags, their evidence, and the rewatch question's answer.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-03

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DRIFT_STAGE = postgresql.ENUM("quiet", "surfaced", name="drift_stage", create_type=False)
DRIFT_OUTCOME = postgresql.ENUM(
    "re_placed",
    "kept",
    "re_pointed",
    "self_resolved",
    name="drift_outcome",
    create_type=False,
)
REWATCH_OUTCOME = postgresql.ENUM(
    "confirmed", "re_placed", "skipped", name="rewatch_outcome", create_type=False
)


def upgrade() -> None:
    DRIFT_STAGE.create(op.get_bind(), checkfirst=True)
    DRIFT_OUTCOME.create(op.get_bind(), checkfirst=True)
    REWATCH_OUTCOME.create(op.get_bind(), checkfirst=True)

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
        sa.Column("stage", DRIFT_STAGE, nullable=False),
        sa.Column("re_placing_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "opened_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", DRIFT_OUTCOME, nullable=True),
    )
    op.create_index("ix_drift_flags_account_id", "drift_flags", ["account_id"])
    op.create_index("ix_drift_flags_account_film_id", "drift_flags", ["account_film_id"])
    # At most one open flag per film: a second could only say the same thing, and the
    # owner would have to resolve one doubt twice.
    op.create_index(
        "uq_drift_flags_open_film",
        "drift_flags",
        ["account_id", "account_film_id"],
        unique=True,
        postgresql_where=sa.text("closed_at IS NULL"),
    )

    # Evidence rows live only while their flag is open, so one judgment hangs on one
    # flag and the uniqueness needs no partial predicate to say so.
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

    # The rewatch question's answer rides on the watch it was asked about: the offer
    # belongs to that moment, so an unanswered one is simply a NULL here.
    op.add_column("watch_events", sa.Column("rewatch_outcome", REWATCH_OUTCOME, nullable=True))


def downgrade() -> None:
    op.drop_column("watch_events", "rewatch_outcome")
    op.drop_table("drift_evidence")
    op.drop_table("drift_flags")
    REWATCH_OUTCOME.drop(op.get_bind(), checkfirst=True)
    DRIFT_OUTCOME.drop(op.get_bind(), checkfirst=True)
    DRIFT_STAGE.drop(op.get_bind(), checkfirst=True)
