"""Cross-cutting invariants of data-model.md that hold structurally, whatever the flow."""

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
