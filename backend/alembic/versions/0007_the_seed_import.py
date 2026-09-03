"""The seed import: the export's rows, and what the account keeps from them.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-02

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

IMPORT_STATUS = postgresql.ENUM("matching", "complete", name="import_status", create_type=False)
IMPORT_ROW_KIND = postgresql.ENUM(
    "rating",
    "watchlist",
    "watched",
    "diary",
    "profile_favorite",
    name="import_row_kind",
    create_type=False,
)
IMPORT_ROW_STATE = postgresql.ENUM(
    "pending",
    "auto_matched",
    "review_pending",
    "bound",
    "unmatched_open",
    "dismissed",
    name="import_row_state",
    create_type=False,
)


def upgrade() -> None:
    IMPORT_STATUS.create(op.get_bind(), checkfirst=True)
    IMPORT_ROW_KIND.create(op.get_bind(), checkfirst=True)
    IMPORT_ROW_STATE.create(op.get_bind(), checkfirst=True)

    # Imported ratings are the owner's own band judgments, so they are logged as such
    # and the dividers they pin name them. The moment needs a name of its own.
    op.execute(
        "ALTER TYPE comparison_context ADD VALUE IF NOT EXISTS 'seed_import' AFTER 'spontaneous'"
    )

    # One import in effect per account: importing again wipes the realm and rebuilds,
    # so a second row could only ever mean a merge, and there is never a merge.
    op.create_table(
        "imports",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Uuid(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_name", sa.String(255), nullable=False),
        sa.Column("status", IMPORT_STATUS, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("account_id", name="uq_imports_account_id"),
    )
    op.create_index("ix_imports_account_id", "imports", ["account_id"])

    op.create_table(
        "import_rows",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Uuid(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "import_id", sa.Uuid(), sa.ForeignKey("imports.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("kind", IMPORT_ROW_KIND, nullable=False),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("letterboxd_uri", sa.String(500), nullable=True),
        sa.Column("rating", sa.Float(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rewatch", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("state", IMPORT_ROW_STATE, nullable=False),
        # RESTRICT, like every other reference into the shared catalog: an account
        # operation never removes a film, and a bound row naming a gone one is a lie.
        sa.Column(
            "film_id",
            sa.Integer(),
            sa.ForeignKey("films.tmdb_id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "candidates", postgresql.ARRAY(sa.Integer()), server_default="{}", nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_import_rows_account_id", "import_rows", ["account_id"])
    op.create_index("ix_import_rows_import_id", "import_rows", ["import_id"])

    # What Letterboxd holds for a rated film, as far as Anchor knows: the seed import
    # writes it once and only the owner marking a film synced ever writes it again.
    op.add_column("account_films", sa.Column("last_synced_rating", sa.Float(), nullable=True))
    op.add_column(
        "watch_events",
        sa.Column("rewatch", sa.Boolean(), server_default="false", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("watch_events", "rewatch")
    op.drop_column("account_films", "last_synced_rating")
    op.drop_table("import_rows")
    op.drop_table("imports")
    IMPORT_ROW_STATE.drop(op.get_bind(), checkfirst=True)
    IMPORT_ROW_KIND.drop(op.get_bind(), checkfirst=True)
    IMPORT_STATUS.drop(op.get_bind(), checkfirst=True)
