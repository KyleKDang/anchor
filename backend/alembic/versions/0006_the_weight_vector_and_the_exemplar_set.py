"""The taste profile's numeric half: weight vector, exemplar set, and the metrics log.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-02

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EXEMPLAR_ROLE = postgresql.ENUM("anchor", "best", "worst", name="exemplar_role", create_type=False)


def upgrade() -> None:
    EXEMPLAR_ROLE.create(op.get_bind(), checkfirst=True)

    # Current-only: one fit per account, replaced wholesale on every retrain.
    op.create_table(
        "weight_vectors",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Uuid(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("weights", postgresql.JSONB(), nullable=False),
        sa.Column("space", postgresql.JSONB(), nullable=False),
        sa.Column("training_pairs", sa.Integer(), nullable=False),
        sa.Column(
            "trained_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("account_id", name="uq_weight_vectors_account_id"),
    )
    op.create_index("ix_weight_vectors_account_id", "weight_vectors", ["account_id"])

    op.create_table(
        "exemplars",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Uuid(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "film_id",
            sa.Integer(),
            sa.ForeignKey("films.tmdb_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("role", EXEMPLAR_ROLE, nullable=False),
        sa.Column("band", sa.Float(), nullable=True),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column(
            "computed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("account_id", "role", "rank", name="uq_exemplars_account_role_rank"),
    )
    op.create_index("ix_exemplars_account_id", "exemplars", ["account_id"])

    # Append-only: one row per retrain, never updated and never deleted short of the
    # account-realm wipe, which is what makes it readable as a history.
    op.create_table(
        "taste_metrics",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Uuid(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("held_out_accuracy", sa.Float(), nullable=True),
        sa.Column("held_out_pairs", sa.Integer(), nullable=False),
        sa.Column("training_pairs", sa.Integer(), nullable=False),
        sa.Column("rated_films", sa.Integer(), nullable=False),
        sa.Column("explicit_comparisons", sa.Integer(), nullable=False),
        sa.Column("settled_films", sa.Integer(), nullable=False),
        sa.Column("bands_spanned", sa.Integer(), nullable=False),
        sa.Column(
            "computed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_taste_metrics_account_id", "taste_metrics", ["account_id"])
    op.create_index("ix_taste_metrics_computed_at", "taste_metrics", ["computed_at"])


def downgrade() -> None:
    op.drop_table("taste_metrics")
    op.drop_table("exemplars")
    op.drop_table("weight_vectors")
    EXEMPLAR_ROLE.drop(op.get_bind(), checkfirst=True)
