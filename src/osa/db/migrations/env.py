"""Alembic environment: online migrations only, URL from Settings or the caller."""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

from osa.db.models import Base
from osa.db.session import database_url

config = context.config
if config.config_file_name is not None and "url" not in config.attributes:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def run_migrations_online() -> None:
    explicit = config.attributes.get("url")
    engine = create_engine(explicit if explicit is not None else database_url())
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    raise RuntimeError("offline migrations are not supported; connect to a database")
run_migrations_online()
