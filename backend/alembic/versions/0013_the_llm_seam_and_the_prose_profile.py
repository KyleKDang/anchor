"""The spend ledger, profile constraints, and the versioned prose profile.

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-04

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013"
down_revision: str | Sequence[str] | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LLM_OPERATION = postgresql.ENUM(
    "rerank_candidates",
    "regenerate_prose_profile",
    "tag_film_qualities",
    "suggest_qualities",
    name="llm_operation",
    create_type=False,
)
CONSTRAINT_KIND = postgresql.ENUM(
    "quality_pick", "prose_correction", name="constraint_kind", create_type=False
)
PROSE_TRIGGER = postgresql.ENUM(
    "first",
    "placements",
    "anchors",
    "drift",
    "constraints",
    "staleness",
    name="prose_trigger",
    create_type=False,
)


def upgrade() -> None:
    LLM_OPERATION.create(op.get_bind(), checkfirst=True)
    CONSTRAINT_KIND.create(op.get_bind(), checkfirst=True)
    PROSE_TRIGGER.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "spend_ledger_entries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        # Nullable: NULL is the shared scope, work paid for once for everybody.
        sa.Column(
            "account_id", sa.Uuid(), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=True
        ),
        sa.Column("operation", LLM_OPERATION, nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_micros", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_spend_ledger_entries_account_id", "spend_ledger_entries", ["account_id"])
    # Both cap checks are a month-to-date sum, so the timestamp is what they scan on:
    # the global one has no account to narrow by and reads the whole table every time.
    op.create_index("ix_spend_ledger_entries_created_at", "spend_ledger_entries", ["created_at"])

    op.create_table(
        "profile_constraints",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Uuid(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", CONSTRAINT_KIND, nullable=False),
        # Deferred, like the comparison log's quality reference: the guard wanted is "a
        # quality is never dropped out from under a constraint naming it", but account
        # deletion cascades into both tables in whatever order it likes.
        sa.Column(
            "quality_id",
            sa.Uuid(),
            sa.ForeignKey(
                "quality_list_entries.id",
                ondelete="NO ACTION",
                deferrable=True,
                initially="DEFERRED",
            ),
            nullable=True,
        ),
        sa.Column("content", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("lifted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(quality_id IS NOT NULL) = (kind = 'quality_pick')",
            name="ck_profile_constraints_quality_pick",
        ),
        sa.CheckConstraint(
            "(content IS NOT NULL) = (kind = 'prose_correction')",
            name="ck_profile_constraints_prose_correction",
        ),
    )
    op.create_index("ix_profile_constraints_account_id", "profile_constraints", ["account_id"])
    op.create_index("ix_profile_constraints_quality_id", "profile_constraints", ["quality_id"])
    op.create_index(
        "uq_profile_constraints_active_quality",
        "profile_constraints",
        ["account_id", "quality_id"],
        unique=True,
        postgresql_where=sa.text("lifted_at IS NULL AND quality_id IS NOT NULL"),
    )

    op.create_table(
        "prose_profile_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Uuid(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("text", sa.String(), nullable=False),
        sa.Column("trigger", PROSE_TRIGGER, nullable=False),
        sa.Column("placements", sa.Integer(), nullable=False),
        sa.Column("explicit_comparisons", sa.Integer(), nullable=False),
        sa.Column("drift_resolutions", sa.Integer(), nullable=False),
        sa.Column("anchors", sa.String(64), nullable=False),
        sa.Column("constraints", sa.String(64), nullable=False),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("account_id", "version"),
    )
    op.create_index(
        "ix_prose_profile_versions_account_id", "prose_profile_versions", ["account_id"]
    )


def downgrade() -> None:
    op.drop_table("prose_profile_versions")
    op.drop_table("profile_constraints")
    op.drop_table("spend_ledger_entries")
    PROSE_TRIGGER.drop(op.get_bind(), checkfirst=True)
    CONSTRAINT_KIND.drop(op.get_bind(), checkfirst=True)
    LLM_OPERATION.drop(op.get_bind(), checkfirst=True)
