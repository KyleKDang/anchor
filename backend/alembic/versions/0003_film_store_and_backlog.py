"""The shared film catalog and the account-film lifecycle.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-01

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# create_type=False: the type is created once, explicitly, below - otherwise
# ``create_table`` would emit a second unconditional CREATE TYPE and collide.
LIFECYCLE_STATE = postgresql.ENUM(
    "backlog", "watched_unrated", "rated", name="lifecycle_state", create_type=False
)


def upgrade() -> None:
    op.create_table(
        "films",
        # The TMDB id is the identity: the catalog is shared, so a surrogate key would
        # only add a second name for the same film.
        sa.Column("tmdb_id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("release_year", sa.Integer(), nullable=True),
        sa.Column("overview", sa.String(), nullable=False, server_default=""),
        sa.Column("poster_path", sa.String(), nullable=True),
        sa.Column("backdrop_path", sa.String(), nullable=True),
        sa.Column("runtime", sa.Integer(), nullable=True),
        sa.Column("genres", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column("keywords", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column("credits", postgresql.JSONB(), nullable=False),
        sa.Column("vote_average", sa.Float(), nullable=False),
        sa.Column("vote_count", sa.Integer(), nullable=False),
        sa.Column(
            "fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_films_release_year", "films", ["release_year"])
    op.create_index("ix_films_fetched_at", "films", ["fetched_at"])

    LIFECYCLE_STATE.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "account_films",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Uuid(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # RESTRICT, not CASCADE: an account wipe must never reach into the shared catalog,
        # and a catalog row is only ever deleted once nothing references it.
        sa.Column(
            "film_id",
            sa.Integer(),
            sa.ForeignKey("films.tmdb_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("state", LIFECYCLE_STATE, nullable=False),
        sa.Column("rate_later", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column(
            "added_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("account_id", "film_id", name="uq_account_films_account_id_film_id"),
    )
    op.create_index("ix_account_films_account_id", "account_films", ["account_id"])
    op.create_index("ix_account_films_film_id", "account_films", ["film_id"])


def downgrade() -> None:
    op.drop_table("account_films")
    LIFECYCLE_STATE.drop(op.get_bind(), checkfirst=True)
    op.drop_table("films")
