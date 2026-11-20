import asyncio
from datetime import date

import pytest
from redis.asyncio import Redis

from app.core.redis import LockManager, LockNotAcquiredError, QuotaCounter, RateLimiter, ReservationStatus

DAY = date(2026, 11, 20)


@pytest.fixture
def locks(redis: Redis, redis_prefix: str) -> LockManager:
    return LockManager(redis, prefix=redis_prefix, default_ttl_s=2, poll_interval_s=0.02, max_wait_s=2)


async def test_lock_is_exclusive_and_released_by_owner_only(locks: LockManager) -> None:
    lease = await locks.try_acquire("client:1:run")
    assert lease is not None
    assert await locks.try_acquire("client:1:run") is None
    forged = type(lease)(key=lease.key, token="not-the-owner", ttl_s=lease.ttl_s)
    assert not await locks.release(forged)
    assert await locks.release(lease)
    assert await locks.try_acquire("client:1:run") is not None


async def test_acquire_times_out_when_held(locks: LockManager) -> None:
    await locks.try_acquire("busy", ttl_s=5)
    with pytest.raises(LockNotAcquiredError):
        async with locks.acquire("busy", wait_s=0.1):
            pass


async def test_lease_is_renewed_while_held(locks: LockManager, redis: Redis) -> None:
    async with locks.acquire("long", ttl_s=0.3):
        await asyncio.sleep(0.6)
        assert await redis.exists(locks.lock_key("long"))
    assert not await redis.exists(locks.lock_key("long"))


async def test_single_flight_runs_once_for_concurrent_callers(locks: LockManager) -> None:
    calls = 0
    fresh = False

    async def sync_channel() -> str:
        nonlocal calls, fresh
        calls += 1
        await asyncio.sleep(0.1)
        fresh = True
        return "synced"

    async def already_fresh() -> bool:
        return fresh

    results = await asyncio.gather(
        *(locks.single_flight("yt:channel:UCx:sync", sync_channel, skip_if=already_fresh) for _ in range(3))
    )
    assert calls == 1
    assert sorted(result.ran for result in results) == [False, False, True]
    assert [result.value for result in results if result.ran] == ["synced"]


async def test_rate_limiter_waits_when_bucket_is_empty(redis: Redis, redis_prefix: str) -> None:
    limiter = RateLimiter(redis, prefix=redis_prefix)
    assert await limiter.try_take("tavily", rate_per_s=10, capacity=2) == 0
    assert await limiter.try_take("tavily", rate_per_s=10, capacity=2) == 0
    assert await limiter.try_take("tavily", rate_per_s=10, capacity=2) > 0
    with pytest.raises(ValueError):
        await limiter.try_take("tavily", rate_per_s=10, capacity=2, cost=3)


async def test_quota_counter_enforces_limits(redis: Redis, redis_prefix: str) -> None:
    quota = QuotaCounter(redis, prefix=redis_prefix, ttl_s=60)
    assert (await quota.reserve("search", DAY, 1, limit=2, client_id="c1", client_cap=1)).ok
    capped = await quota.reserve("search", DAY, 1, limit=2, client_id="c1", client_cap=1)
    assert capped.status is ReservationStatus.over_client_cap
    assert (await quota.reserve("search", DAY, 1, limit=2, client_id="c2")).used == 2
    over = await quota.reserve("search", DAY, 1, limit=2)
    assert over.status is ReservationStatus.over_limit
    await quota.refund("search", DAY, 1, client_id="c2")
    assert await quota.used("search", DAY) == 1
    assert await quota.used("search", DAY, client_id="c2") == 0
    await quota.mark_exhausted("search", DAY)
    assert (await quota.reserve("search", DAY, 1, limit=100)).status is ReservationStatus.exhausted


async def test_quota_counter_rebuilds_from_ledger_totals(redis: Redis, redis_prefix: str) -> None:
    quota = QuotaCounter(redis, prefix=redis_prefix, ttl_s=60)
    await quota.rebuild("main", DAY, 9_990)
    assert not (await quota.reserve("main", DAY, 20, limit=10_000)).ok
    assert (await quota.reserve("main", DAY, 10, limit=10_000)).used == 10_000
