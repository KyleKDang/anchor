"""Drop the health check's probe table; the worker is proven by its heartbeat now.

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-05

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | Sequence[str] | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nothing reads these rows once the check stops round-tripping a probe (#82), and
    # nothing else ever wrote them, so the table goes rather than being left to fill.
    op.drop_table("worker_probes")


def downgrade() -> None:
    op.create_table(
        "worker_probes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
    )
