from alembic import context
from sqlalchemy import create_engine

import anchor.models  # noqa: F401  (register models on the metadata)
from anchor.db import Base
from anchor.settings import Settings

config = context.config
target_metadata = Base.metadata


def database_url() -> str:
    return config.get_main_option("sqlalchemy.url") or Settings().sqlalchemy_url


def run_migrations_offline() -> None:
    context.configure(url=database_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(database_url())
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
