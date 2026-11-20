import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from pydantic_settings import SettingsConfigDict
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import LEGACY_ENV_PATHS, Settings
from app.core.db import SessionFactory

BACKEND_DIR = Path(__file__).resolve().parents[1]
TEST_DATABASE_URL = os.environ.get(
    "GH_TEST_DATABASE_URL", "postgresql+asyncpg://growth:growth@localhost:5432/growthhacker_test"
)
TEST_REDIS_URL = os.environ.get("GH_TEST_REDIS_URL", "redis://localhost:6379/15")


# ignores the repo .env so tests never pick up real credentials
class IsolatedSettings(Settings):
    model_config = SettingsConfigDict(**{**Settings.model_config, "env_file": None})


def make_settings(**overrides) -> Settings:
    defaults = {
        "app": {"env": "test"},
        "db": {"url": TEST_DATABASE_URL},
        "redis": {"url": TEST_REDIS_URL, "key_prefix": f"ghtest:{uuid.uuid4().hex[:8]}"},
    }
    return IsolatedSettings(**{**defaults, **overrides})


def alembic_config(database_url: str) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    config.attributes["configure_logger"] = False
    return config


@pytest.fixture(autouse=True)
def _no_ambient_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if (name.startswith("GH_") and not name.startswith("GH_TEST_")) or name in LEGACY_ENV_PATHS:
            monkeypatch.delenv(name)


@pytest.fixture(scope="session")
def migrated_database() -> str:
    command.upgrade(alembic_config(TEST_DATABASE_URL), "head")
    return TEST_DATABASE_URL


@pytest.fixture(scope="session")
async def engine(migrated_database: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(migrated_database, connect_args={"server_settings": {"timezone": "UTC"}})
    yield engine
    await engine.dispose()


@pytest.fixture
async def connection(engine: AsyncEngine) -> AsyncIterator[AsyncConnection]:
    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            yield connection
        finally:
            await transaction.rollback()


@pytest.fixture
def session_factory(connection: AsyncConnection) -> SessionFactory:
    # sessions join the per-test transaction through savepoints, so commits never escape the test
    return async_sessionmaker(
        bind=connection, expire_on_commit=False, autoflush=False, join_transaction_mode="create_savepoint"
    )


@pytest.fixture
async def session(session_factory: SessionFactory) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session


@pytest.fixture
async def redis() -> AsyncIterator[Redis]:
    client = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    await client.ping()
    yield client
    await client.aclose()


@pytest.fixture
async def redis_prefix(redis: Redis) -> AsyncIterator[str]:
    prefix = f"ghtest:{uuid.uuid4().hex[:8]}"
    yield prefix
    keys = [key async for key in redis.scan_iter(match=f"{prefix}:*")]
    if keys:
        await redis.delete(*keys)
