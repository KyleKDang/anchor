"""Cross-cutting invariants of data-model.md that hold structurally, whatever the flow."""

import subprocess
import sys
import textwrap

from sqlalchemy import text

from invariants import ACCOUNT_TABLE, account_realm_tables


async def test_every_account_realm_table_is_wiped_with_its_account(db):
    """Deleting an account row must cascade to every table that carries ``account_id``."""
    async with db.sessions() as session:
        realm = await account_realm_tables(session)
        cascading = await session.execute(
            text(
                """
                SELECT kcu.table_name, rc.delete_rule
                FROM information_schema.referential_constraints rc
                JOIN information_schema.key_column_usage kcu
                  ON kcu.constraint_name = rc.constraint_name
                 AND kcu.constraint_schema = rc.constraint_schema
                JOIN information_schema.constraint_column_usage ccu
                  ON ccu.constraint_name = rc.constraint_name
                 AND ccu.constraint_schema = rc.constraint_schema
                WHERE rc.constraint_schema = current_schema()
                  AND kcu.column_name = 'account_id'
                  AND ccu.table_name = :accounts AND ccu.column_name = 'id'
                """
            ),
            {"accounts": ACCOUNT_TABLE},
        )
        rules = {table: rule for table, rule in cascading}

    assert realm, "no account-realm tables found"
    assert {table: rules.get(table) for table in realm} == dict.fromkeys(realm, "CASCADE")


def test_the_web_process_never_loads_the_llm_module():
    """No interactive request path can wait on an LLM call, because it cannot make one.

    architecture.md puts the precompute-only rule (taste-profile.md) on the import graph
    rather than on anybody's discipline: only the worker imports :mod:`anchor.llm`, so a
    screen that waited on a provider would first have to add an import - which is exactly
    the change this refuses.

    Run in a subprocess because the test process has already imported the seam to build
    its fake; the question is what the *web app* pulls in, and only a fresh interpreter
    can answer it.
    """
    program = textwrap.dedent(
        """
        import sys
        import anchor.main

        assert "anchor.llm" not in sys.modules, sorted(
            name for name in sys.modules if name.startswith("anchor.")
        )
        """
    )
    done = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True)

    assert done.returncode == 0, done.stderr


def test_the_worker_process_does_load_it():
    """The other half of the same claim: the rule is about where, not about whether."""
    program = "import sys, anchor.worker; assert 'anchor.llm' in sys.modules"
    done = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True)

    assert done.returncode == 0, done.stderr
