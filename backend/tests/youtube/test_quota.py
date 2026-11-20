import asyncio
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from redis.asyncio import Redis

from app.core.clock import FrozenClock
from app.core.config import QuotaSettings
from app.core.redis import QuotaCounter
from app.domain.youtube import QuotaBucket, QuotaPriority
from app.ports.quota import QuotaExhausted
from app.services.youtube.context import SearchPool, request_context
from app.services.youtube.quota import PacificDay, QuotaManager

NOW = datetime(2026, 11, 20, 9, 30, tzinfo=UTC)


def manager(redis: Redis, prefix: str, clock: FrozenClock | None = None, **overrides) -> QuotaManager:
    settings = QuotaSettings(**overrides)
    counter = QuotaCounter(redis, prefix=prefix, project=settings.project)
    return QuotaManager(counter, redis, settings, clock or FrozenClock(NOW), prefix=prefix)


def ctx(priority: QuotaPriority = QuotaPriority.interactive, **kwargs):
    return request_context(priority, **kwargs)


async def test_concurrent_reservations_never_exceed_the_limit(redis: Redis, redis_prefix: str) -> None:
    quota = manager(redis, redis_prefix, main_daily_units=150)

    async def attempt() -> bool:
        try:
            await quota.reserve(QuotaBucket.main, 1, ctx(), "videos.list")
            return True
        except QuotaExhausted:
            return False

    results = await asyncio.gather(*(attempt() for _ in range(200)))
    assert sum(results) == 150
    assert await quota.remaining(QuotaBucket.main) == 0


async def test_priority_ceilings(redis: Redis, redis_prefix: str) -> None:
    quota = manager(redis, redis_prefix, main_daily_units=100)
    for _ in range(60):
        await quota.reserve(QuotaBucket.main, 1, ctx(QuotaPriority.background), "videos.list")
    with pytest.raises(QuotaExhausted):
        await quota.reserve(QuotaBucket.main, 1, ctx(QuotaPriority.background), "videos.list")
    for _ in range(25):
        await quota.reserve(QuotaBucket.main, 1, ctx(QuotaPriority.daily_refresh), "videos.list")
    with pytest.raises(QuotaExhausted):
        await quota.reserve(QuotaBucket.main, 1, ctx(QuotaPriority.daily_refresh), "videos.list")
    await quota.reserve(QuotaBucket.main, 15, ctx(QuotaPriority.interactive), "videos.list")
    with pytest.raises(QuotaExhausted):
        await quota.reserve(QuotaBucket.search, 1, ctx(QuotaPriority.snapshot), "search.list")


async def test_per_client_caps_stop_a_noisy_tenant(redis: Redis, redis_prefix: str) -> None:
    quota = manager(redis, redis_prefix, client_daily_units=5)
    noisy, quiet = uuid.uuid4(), uuid.uuid4()
    for _ in range(5):
        await quota.reserve(QuotaBucket.main, 1, ctx(client_id=noisy), "videos.list")
    with pytest.raises(QuotaExhausted):
        await quota.reserve(QuotaBucket.main, 1, ctx(client_id=noisy), "videos.list")
    await quota.reserve(QuotaBucket.main, 1, ctx(client_id=quiet), "videos.list")
    assert await quota.used(QuotaBucket.main, noisy) == 5


async def test_search_pools_are_reserved_separately(redis: Redis, redis_prefix: str) -> None:
    quota = manager(redis, redis_prefix)
    for _ in range(15):
        await quota.reserve(QuotaBucket.search, 1, ctx(search_pool=SearchPool.news), "search.list")
    with pytest.raises(QuotaExhausted):
        await quota.reserve(QuotaBucket.search, 1, ctx(search_pool=SearchPool.news), "search.list")
    reservation = await quota.reserve(QuotaBucket.search, 1, ctx(), "search.list")
    assert reservation.pool_bucket == "search_pool_onboarding"
    assert await quota.used(QuotaBucket.search) == 16


async def test_run_budget_is_enforced_and_refunds_roll_back(redis: Redis, redis_prefix: str) -> None:
    quota = manager(redis, redis_prefix)
    run_id = uuid.uuid4()
    await quota.open_run_budget(run_id, {QuotaBucket.main: 3})
    reservations = [
        await quota.reserve(QuotaBucket.main, 1, ctx(run_id=run_id), "videos.list") for _ in range(3)
    ]
    with pytest.raises(QuotaExhausted):
        await quota.reserve(QuotaBucket.main, 1, ctx(run_id=run_id), "videos.list")
    await quota.refund(reservations[0], ctx(run_id=run_id))
    assert (await quota.run_usage(run_id))[QuotaBucket.main] == 2
    assert await quota.used(QuotaBucket.main) == 2


async def test_exhausted_flag_after_google_says_so(redis: Redis, redis_prefix: str) -> None:
    quota = manager(redis, redis_prefix)
    resets_at = await quota.mark_exhausted(QuotaBucket.main)
    assert resets_at == datetime(2026, 11, 21, 8, 0, tzinfo=UTC)
    with pytest.raises(QuotaExhausted) as raised:
        await quota.reserve(QuotaBucket.main, 1, ctx(), "videos.list")
    assert raised.value.resets_at == resets_at
    await quota.reserve(QuotaBucket.batch_stats, 1, ctx(QuotaPriority.snapshot), "videos.batchGetStats")


async def test_pacific_day_rolls_at_midnight_including_dst() -> None:
    pacific = PacificDay()
    assert pacific.day(datetime(2026, 11, 20, 7, 59, tzinfo=UTC)) == date(2026, 11, 19)
    assert pacific.day(datetime(2026, 11, 20, 8, 0, tzinfo=UTC)) == date(2026, 11, 20)
    assert pacific.day(datetime(2026, 7, 1, 6, 59, tzinfo=UTC)) == date(2026, 6, 30)
    assert pacific.day(datetime(2026, 7, 1, 7, 0, tzinfo=UTC)) == date(2026, 7, 1)
    assert pacific.resets_at(datetime(2026, 3, 7, 12, tzinfo=UTC)) == datetime(2026, 3, 8, 8, tzinfo=UTC)
    assert pacific.resets_at(datetime(2026, 3, 8, 12, tzinfo=UTC)) == datetime(2026, 3, 9, 7, tzinfo=UTC)
    assert pacific.resets_at(datetime(2026, 10, 31, 12, tzinfo=UTC)) == datetime(2026, 11, 1, 7, tzinfo=UTC)
    assert pacific.resets_at(datetime(2026, 11, 1, 12, tzinfo=UTC)) == datetime(2026, 11, 2, 8, tzinfo=UTC)


async def test_counters_are_per_pacific_day(redis: Redis, redis_prefix: str) -> None:
    clock = FrozenClock(datetime(2026, 11, 20, 7, 30, tzinfo=UTC))
    quota = manager(redis, redis_prefix, clock, main_daily_units=1)
    await quota.reserve(QuotaBucket.main, 1, ctx(), "videos.list")
    with pytest.raises(QuotaExhausted):
        await quota.reserve(QuotaBucket.main, 1, ctx(), "videos.list")
    clock.advance(timedelta(minutes=31))
    await quota.reserve(QuotaBucket.main, 1, ctx(), "videos.list")


async def test_redis_outage_fails_closed_except_small_interactive_allowance(redis_prefix: str) -> None:
    dead = Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.2)
    quota = manager(dead, redis_prefix, redis_down_interactive_units=2)
    with pytest.raises(QuotaExhausted):
        await quota.reserve(QuotaBucket.main, 1, ctx(QuotaPriority.daily_refresh), "videos.list")
    first = await quota.reserve(QuotaBucket.main, 1, ctx(), "videos.list")
    await quota.reserve(QuotaBucket.main, 1, ctx(), "videos.list")
    with pytest.raises(QuotaExhausted):
        await quota.reserve(QuotaBucket.main, 1, ctx(), "videos.list")
    await quota.refund(first, ctx())
    await quota.reserve(QuotaBucket.main, 1, ctx(), "videos.list")
    await dead.aclose()
