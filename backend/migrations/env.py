import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

import app.models
from app.core.config import get_settings
from app.models.youtube_cache import PARTITIONED_TABLES

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = app.models.Base.metadata
PARTITION_PREFIXES = tuple(f"{table}_p" for table in PARTITIONED_TABLES)


def database_url() -> str:
    return (
        context.get_x_argument(as_dictionary=True).get("db_url")
        or config.get_main_option("sqlalchemy.url")
        or get_settings().db.url.get_secret_value()
    )


def include_object(obj, name, type_, reflected, compare_to) -> bool:
    if type_ == "table" and name and name.startswith(PARTITION_PREFIXES):
        return False
    return not (type_ == "table" and reflected and compare_to is None and name == "alembic_version")


def configure(connection: Connection | None = None, url: str | None = None) -> None:
    context.configure(
        connection=connection,
        url=url,
        target_metadata=target_metadata,
        include_object=include_object,
        compare_type=True,
        compare_server_default=False,
        literal_binds=connection is None,
    )


def run_migrations_offline() -> None:
    configure(url=database_url())
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    configure(connection=connection)
    with context.begin_transaction():
        connection.execute(text("SELECT pg_advisory_xact_lock(hashtext('gh_alembic'))"))
        connection.execute(text("SET LOCAL lock_timeout = '10s'"))
        connection.execute(text("SET LOCAL statement_timeout = '0'"))
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = async_engine_from_config(
        {"sqlalchemy.url": database_url()},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
