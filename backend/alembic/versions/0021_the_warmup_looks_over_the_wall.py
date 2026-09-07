"""The warmup's middle step on the import fill: looking over the wall in edit mode.

Revision ID: 0021
Revises: 0020
Create Date: 2026-09-07

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0021"
down_revision: str | Sequence[str] | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # How far the step has got is derived - a placement carries the moment it was last
    # moved - so the only new fact is a skip, which is what every other warmup mark is.
    # After 'anchors' because that is where the step sits in the run.
    op.execute("ALTER TYPE warmup_mark ADD VALUE IF NOT EXISTS 'wall' AFTER 'anchors'")


def downgrade() -> None:
    # Postgres cannot drop one value from an enum, so the type is rebuilt without it and
    # the rows that used it go: a skip of a step that no longer exists is not a fact.
    op.execute("DELETE FROM warmup_progress WHERE mark = 'wall'")
    op.execute("ALTER TYPE warmup_mark RENAME TO warmup_mark_old")
    op.execute(
        "CREATE TYPE warmup_mark AS ENUM ('entered', 'anchors', 'rating', 'backlog', 'dismissed')"
    )
    op.execute(
        """
        ALTER TABLE warmup_progress
        ALTER COLUMN mark TYPE warmup_mark USING mark::text::warmup_mark
        """
    )
    op.execute("DROP TYPE warmup_mark_old")
