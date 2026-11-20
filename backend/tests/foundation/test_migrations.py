import asyncio
import uuid

import asyncpg
import pytest
from alembic import command
from sqlalchemy.engine import make_url

from tests.conftest import TEST_DATABASE_URL, alembic_config

pytestmark = pytest.mark.db


def _dsn(database: str | None = None) -> str:
    url = make_url(TEST_DATABASE_URL).set(drivername="postgresql")
    if database is not None:
        url = url.set(database=database)
    return url.render_as_string(hide_password=False)


async def _admin(sql: str) -> None:
    connection = await asyncpg.connect(_dsn())
    try:
        await connection.execute(sql)
    finally:
        await connection.close()


async def _fetch(database: str, sql: str) -> list[asyncpg.Record]:
    connection = await asyncpg.connect(_dsn(database))
    try:
        return await connection.fetch(sql)
    finally:
        await connection.close()


@pytest.fixture
def scratch_database():
    name = f"gh_migrations_{uuid.uuid4().hex[:8]}"
    asyncio.run(_admin(f'CREATE DATABASE "{name}"'))
    yield name
    asyncio.run(_admin(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


def test_upgrade_downgrade_round_trip(scratch_database: str) -> None:
    url = make_url(TEST_DATABASE_URL).set(database=scratch_database).render_as_string(hide_password=False)
    config = alembic_config(url)

    command.upgrade(config, "head")
    command.check(config)
    tables = asyncio.run(
        _fetch(scratch_database, "SELECT count(*) AS n FROM pg_tables WHERE schemaname = 'public'")
    )
    partitions = asyncio.run(
        _fetch(
            scratch_database, "SELECT relname FROM pg_class WHERE relname ~ '^yt_video_snapshots_p[0-9]{8}$'"
        )
    )
    views = asyncio.run(
        _fetch(scratch_database, "SELECT viewname FROM pg_views WHERE viewname = 'v_tracked_channels'")
    )
    assert tables[0]["n"] > 100
    assert len(partitions) >= 8
    assert len(views) == 1

    command.downgrade(config, "-1")
    command.upgrade(config, "head")
    command.downgrade(config, "base")
    remaining = asyncio.run(
        _fetch(scratch_database, "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1")
    )
    assert [row["tablename"] for row in remaining] == ["alembic_version"]
    command.upgrade(config, "head")
