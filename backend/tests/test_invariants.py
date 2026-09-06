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


WRITERS = ("land", "re_rate", "unrate")
"""The three functions in :mod:`anchor.ordering` that move a film's band or rank."""

OWNER_ACTS = ("anchor.placement", "anchor.anchors", "anchor.seeding")
"""Where the owner's picks, re-rates and marks land, and the import that carries them in.

The import is the owner's own Letterboxd ratings arriving, which the spec counts as their
judgments everywhere else too (onboarding-and-import.md). Edit mode joins this list with
the ticket that builds it. :mod:`anchor.ordering` is absent because it is the seam itself:
it is the only module that writes these columns at all, and what is under test is who is
allowed to reach it.
"""


def test_nothing_but_the_owners_own_acts_writes_the_ordering():
    """The engine is read-only on the band, the rank, and the anchor mark.

    Checked structurally rather than flow by flow, because the claim is about every flow
    there will ever be: the advisory math, the jobs, and every read surface are the ones
    that must never move a film, and a new module that did would have to be added to
    :data:`OWNER_ACTS` deliberately.

    Three ways in are checked, which is all there are: calling one of the ordering
    module's writers, building a ``Placement`` row by hand, and setting an anchor mark.
    """
    program = textwrap.dedent(
        f"""
        import ast, pathlib

        writers = {WRITERS!r}
        allowed = {(*OWNER_ACTS, "anchor.ordering")!r}
        offenders = []
        for path in sorted(pathlib.Path("src/anchor").glob("*.py")):
            module = "anchor." + path.stem
            if module in allowed:
                continue
            for node in ast.walk(ast.parse(path.read_text())):
                called = getattr(node, "func", None)
                if isinstance(called, ast.Attribute) and called.attr in writers:
                    offenders.append((module, called.attr, node.lineno))
                if isinstance(called, ast.Name) and called.id == "Placement":
                    offenders.append((module, "Placement()", node.lineno))
                targets = node.targets if isinstance(node, ast.Assign) else []
                for target in targets:
                    if isinstance(target, ast.Attribute) and target.attr == "anchored_at":
                        offenders.append((module, "anchored_at", node.lineno))
        assert not offenders, offenders
        """
    )
    done = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True)

    assert done.returncode == 0, done.stdout + done.stderr
