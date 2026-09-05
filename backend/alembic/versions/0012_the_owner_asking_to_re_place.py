"""The fourth door into a re-placement: the owner asking for one outright.

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-04

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | Sequence[str] | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The ask itself, because the search keeps no state of its own and the other three
    # doors each mark something that already exists. Rows are kept rather than cleared:
    # the request expires against the placement's clock, so what the owner asked for and
    # when stays readable after the placement it opened has landed.
    op.create_table(
        "replacement_requests",
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
        sa.Column(
            "requested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_replacement_requests_account_id", "replacement_requests", ["account_id"])
    op.create_index(
        "ix_replacement_requests_account_film_id", "replacement_requests", ["account_film_id"]
    )


def downgrade() -> None:
    op.drop_table("replacement_requests")
