"""The shared quality tags and the once-ever marker that stops them being re-bought.

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-06

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | Sequence[str] | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable and unstamped for every film already in the catalog: none of them has been
    # tagged, which is exactly what NULL says, so they are tagged the next time an
    # account's activity asks for them rather than in a backfill nobody budgeted for.
    op.add_column("films", sa.Column("tagged_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "quality_tags",
        sa.Column(
            "film_id",
            sa.Integer(),
            sa.ForeignKey("films.tmdb_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        # The composite primary key is the data model's own key, (film, vocabulary
        # quality), so a film cannot carry the same tag twice and the tagging job can
        # re-run without checking first.
        sa.Column("quality", sa.String(64), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_table("quality_tags")
    op.drop_column("films", "tagged_at")
