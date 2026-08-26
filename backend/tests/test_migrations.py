"""The migration chain applies and reverses cleanly on a fresh database."""

import uuid

from conftest import _admin, _url_for, migrate


def test_migrations_upgrade_downgrade_and_upgrade_again():
    name = f"anchor_test_migrations_{uuid.uuid4().hex[:8]}"
    _admin(f'CREATE DATABASE "{name}"')
    try:
        url = _url_for(name)
        migrate(url, "head")
        migrate(url, "base")
        migrate(url, "head")
    finally:
        _admin(f'DROP DATABASE "{name}" WITH (FORCE)')
