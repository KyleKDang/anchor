"""The discovery shelf: verdicts, suggestions, dismissals, and the feed's bookkeeping.

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-06

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0018"
down_revision: str | Sequence[str] | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FIT_BUCKET = postgresql.ENUM(
    "strong_fit", "plausible", "poor_fit", name="fit_bucket", create_type=False
)


def upgrade() -> None:
    FIT_BUCKET.create(op.get_bind(), checkfirst=True)

    # Nullable and unfilled for the films already in the catalog: the rolling re-sync
    # fills each one in as it comes round, and a prefilter that cannot read a language
    # simply does not exclude on it, which is the honest behaviour for a film we have
    # not re-fetched yet.
    op.add_column("films", sa.Column("original_language", sa.String(16), nullable=True))

    op.create_table(
        "dismissals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Uuid(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "film_id",
            sa.Integer(),
            sa.ForeignKey("films.tmdb_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("lifted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("account_id", "film_id", name="uq_dismissals_account_id_film_id"),
    )

    op.create_table(
        "verdicts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Uuid(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "film_id",
            sa.Integer(),
            sa.ForeignKey("films.tmdb_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        # The cache key the whole feature turns on: a bump writes new rows beside the old
        # ones rather than over them, so a degraded read still has last version's answer.
        sa.Column("profile_version", sa.Integer(), nullable=False),
        sa.Column("fit", FIT_BUCKET, nullable=False),
        sa.Column("explanation", sa.String(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column(
            "computed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "account_id", "film_id", "profile_version", name="uq_verdicts_account_film_version"
        ),
    )

    op.create_table(
        "suggestions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Uuid(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "film_id",
            sa.Integer(),
            sa.ForeignKey("films.tmdb_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        # A suggestion cannot exist without the verdict behind it, which is the never-pad
        # rule written into the schema: no verdict, no row, no card.
        sa.Column(
            "verdict_id",
            sa.Uuid(),
            sa.ForeignKey("verdicts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("account_id", "film_id", name="uq_suggestions_account_id_film_id"),
        # Deferred: rebuilding the shelf rewrites the whole run of positions, and
        # mid-rewrite two rows momentarily share one.
        sa.UniqueConstraint(
            "account_id",
            "position",
            name="uq_suggestions_account_id_position",
            deferrable=True,
            initially="DEFERRED",
        ),
    )

    op.create_table(
        "feed_states",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Uuid(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("restocked_profile_version", sa.Integer(), nullable=True),
        sa.Column("restocked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("account_id", name="uq_feed_states_account_id"),
    )


def downgrade() -> None:
    op.drop_table("feed_states")
    op.drop_table("suggestions")
    # The enum after the only table that uses it, and before the rest, so the teardown
    # reads in the same order as the build rather than doubling back.
    op.drop_table("verdicts")
    FIT_BUCKET.drop(op.get_bind())
    op.drop_table("dismissals")
    op.drop_column("films", "original_language")
