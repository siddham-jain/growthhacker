from collections.abc import AsyncIterator, Callable

import pytest
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.adapters.youtube.cached import CachedGateway
from app.core.clock import FrozenClock
from app.core.config import YouTubeFreshnessSettings
from app.core.db import SessionFactory, UnitOfWork
from app.core.redis import LockManager
from app.services.youtube.cache_policy import FreshnessPolicy
from app.services.youtube.ledger import CacheHitCounter
from app.services.youtube.single_flight import InflightClaims
from app.services.youtube.store import YouTubeStore
from tests.youtube.fixtures import NOW, FakeGateway


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock(NOW)


@pytest.fixture
def uow_factory(session_factory: SessionFactory) -> Callable[[], UnitOfWork]:
    return lambda: UnitOfWork(session_factory)


@pytest.fixture
def store(uow_factory) -> YouTubeStore:
    return YouTubeStore(uow_factory)


@pytest.fixture
async def committed_store(engine: AsyncEngine) -> AsyncIterator[YouTubeStore]:
    # concurrency tests need one connection per unit of work, so they commit and clean up after themselves
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    yield YouTubeStore(lambda: UnitOfWork(factory))
    async with engine.begin() as connection:
        await connection.execute(text("DELETE FROM yt_search_cache WHERE params->>'q' LIKE 'zz-test%'"))
        await connection.execute(text("DELETE FROM yt_channels WHERE channel_id LIKE 'UCtest%'"))
        await connection.execute(
            text("DELETE FROM yt_channel_resolutions WHERE input_normalized LIKE 'test%'")
        )


@pytest.fixture
def make_cached(store: YouTubeStore, clock: FrozenClock, redis: Redis, redis_prefix: str):
    def build(inner: FakeGateway, *, cache_store: YouTubeStore | None = None, **kwargs) -> CachedGateway:
        return CachedGateway(
            inner,
            cache_store or store,
            FreshnessPolicy(YouTubeFreshnessSettings()),
            clock,
            locks=LockManager(redis, prefix=redis_prefix, poll_interval_s=0.01),
            inflight=InflightClaims(redis, prefix=redis_prefix, poll_s=0.01),
            hits=CacheHitCounter(redis, clock, prefix=redis_prefix),
            coalesce_window_s=0.01,
            **kwargs,
        )

    return build
