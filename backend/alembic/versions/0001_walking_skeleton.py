"""Walking skeleton: the job queue schema and the worker probe table.

Revision ID: 0001
Revises:
Create Date: 2026-08-25

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from procrastinate.schema import SchemaManager

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # procrastinate's own schema (jobs, events, workers, and its SQL functions).
    # Later procrastinate upgrades apply their bundled SQL migrations the same way.
    # The driver would read a bare "%" as a placeholder; escape it as procrastinate's
    # own connector does before applying the schema.
    op.get_bind().exec_driver_sql(SchemaManager.get_schema().replace("%", "%%"))

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


def downgrade() -> None:
    op.drop_table("worker_probes")
    # procrastinate ships no drop script: remove every procrastinate_* table, function,
    # and type in the current schema (triggers and sequences go with their tables).
    op.execute(
        """
        DO $$
        DECLARE
            obj record;
        BEGIN
            FOR obj IN
                SELECT tablename FROM pg_tables
                WHERE schemaname = current_schema() AND tablename LIKE 'procrastinate\_%'
            LOOP
                EXECUTE format('DROP TABLE IF EXISTS %I CASCADE', obj.tablename);
            END LOOP;
            FOR obj IN
                SELECT p.oid::regprocedure AS signature
                FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                WHERE n.nspname = current_schema() AND p.proname LIKE 'procrastinate\_%'
            LOOP
                EXECUTE format('DROP FUNCTION IF EXISTS %s CASCADE', obj.signature);
            END LOOP;
            FOR obj IN
                SELECT t.typname
                FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace
                WHERE n.nspname = current_schema() AND t.typname LIKE 'procrastinate\_%'
                  AND t.typtype IN ('e', 'c')
            LOOP
                EXECUTE format('DROP TYPE IF EXISTS %I CASCADE', obj.typname);
            END LOOP;
        END $$;
        """
    )
