"""The quality list, the criteria log rows, and the frequency the owner controls.

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-03

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

QUALITY_ORIGIN = postgresql.ENUM("built_in", "custom", name="quality_origin", create_type=False)
CRITERIA_FREQUENCY = postgresql.ENUM(
    "adaptive", "often", "sometimes", "rarely", "off", name="criteria_frequency", create_type=False
)

BUILT_IN_QUALITIES = (
    "Acting",
    "Screenplay",
    "Direction",
    "Shots",
    "Score",
    "Message",
    "Tension",
    "Pacing",
    "Emotional impact",
    "Ending",
    "Humor",
    "Rewatchability",
)


def upgrade() -> None:
    QUALITY_ORIGIN.create(op.get_bind(), checkfirst=True)
    CRITERIA_FREQUENCY.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "quality_list_entries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Uuid(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("origin", QUALITY_ORIGIN, nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("account_id", "name", name="uq_quality_list_entries_account_name"),
    )
    op.create_index("ix_quality_list_entries_account_id", "quality_list_entries", ["account_id"])

    op.add_column(
        "accounts",
        sa.Column(
            "criteria_frequency", CRITERIA_FREQUENCY, server_default="adaptive", nullable=False
        ),
    )

    # Deferred, like a placement's slot reference: the guard wanted is "a quality is
    # never dropped out from under a question asked about it", but account deletion
    # cascades into both tables in whatever order it likes.
    op.add_column("comparison_log_entries", sa.Column("quality_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_comparison_log_entries_quality_id",
        "comparison_log_entries",
        "quality_list_entries",
        ["quality_id"],
        ["id"],
        ondelete="NO ACTION",
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_index(
        "ix_comparison_log_entries_quality_id", "comparison_log_entries", ["quality_id"]
    )
    op.create_check_constraint(
        "ck_comparison_log_entries_criteria_quality",
        "comparison_log_entries",
        "(quality_id IS NOT NULL) = (kind = 'criteria')",
    )

    # Accounts that existed before the list did get theirs here rather than at their next
    # login: the list is meant to be there from account creation, and an account whose
    # list is empty would silently never be offered a criteria question.
    op.execute(
        sa.text(
            """
            INSERT INTO quality_list_entries (id, account_id, name, origin, position)
            SELECT gen_random_uuid(), accounts.id, quality.name, 'built_in', quality.position - 1
            FROM accounts
            CROSS JOIN unnest(:names ::text[]) WITH ORDINALITY AS quality(name, position)
            WHERE accounts.verified_at IS NOT NULL
            """
        ).bindparams(names=list(BUILT_IN_QUALITIES))
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_comparison_log_entries_criteria_quality", "comparison_log_entries", type_="check"
    )
    op.drop_constraint(
        "fk_comparison_log_entries_quality_id", "comparison_log_entries", type_="foreignkey"
    )
    op.drop_index("ix_comparison_log_entries_quality_id", "comparison_log_entries")
    op.drop_column("comparison_log_entries", "quality_id")
    op.drop_column("accounts", "criteria_frequency")
    op.drop_table("quality_list_entries")
    CRITERIA_FREQUENCY.drop(op.get_bind(), checkfirst=True)
    QUALITY_ORIGIN.drop(op.get_bind(), checkfirst=True)
