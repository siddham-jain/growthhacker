import asyncio
import contextlib
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from enum import IntEnum

from redis.asyncio import Redis

from app.core.config import RedisSettings

RELEASE_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""

RENEW_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('PEXPIRE', KEYS[1], ARGV[2])
end
return 0
"""

TOKEN_BUCKET_SCRIPT = """
local rate = tonumber(ARGV[1])
local capacity = tonumber(ARGV[2])
local cost = tonumber(ARGV[3])
local clock = redis.call('TIME')
local now = tonumber(clock[1]) * 1000 + math.floor(tonumber(clock[2]) / 1000)
local state = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(state[1]) or capacity
local ts = tonumber(state[2]) or now
tokens = math.min(capacity, tokens + (now - ts) * rate / 1000)
local wait_ms = 0
if tokens >= cost then
  tokens = tokens - cost
else
  wait_ms = math.ceil((cost - tokens) * 1000 / rate)
end
redis.call('HSET', KEYS[1], 'tokens', tostring(tokens), 'ts', now)
redis.call('PEXPIRE', KEYS[1], math.ceil(capacity * 1000 / rate) + 1000)
return wait_ms
"""

QUOTA_RESERVE_SCRIPT = """
local cost = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
local client_cap = tonumber(ARGV[3])
local ttl_s = tonumber(ARGV[4])
if redis.call('EXISTS', KEYS[3]) == 1 then
  return {1, tonumber(redis.call('GET', KEYS[1]) or '0')}
end
local used = tonumber(redis.call('GET', KEYS[1]) or '0')
if used + cost > limit then
  return {2, used}
end
if client_cap > 0 then
  local client_used = tonumber(redis.call('GET', KEYS[2]) or '0')
  if client_used + cost > client_cap then
    return {3, used}
  end
end
used = redis.call('INCRBY', KEYS[1], cost)
redis.call('EXPIRE', KEYS[1], ttl_s)
if client_cap > 0 or ARGV[5] == '1' then
  redis.call('INCRBY', KEYS[2], cost)
  redis.call('EXPIRE', KEYS[2], ttl_s)
end
return {0, used}
"""


class LockNotAcquiredError(TimeoutError):
    pass


@dataclass(frozen=True)
class Lease:
    key: str
    token: str
    ttl_s: float


@dataclass(frozen=True)
class SingleFlightResult[T]:
    ran: bool
    value: T | None


def build_redis(settings: RedisSettings) -> Redis:
    return Redis.from_url(settings.url.get_secret_value(), decode_responses=True)


class LockManager:
    def __init__(
        self,
        redis: Redis,
        *,
        prefix: str = "gh",
        default_ttl_s: float = 60,
        poll_interval_s: float = 0.25,
        max_wait_s: float = 60,
    ) -> None:
        self._redis = redis
        self._prefix = prefix
        self._default_ttl_s = default_ttl_s
        self._poll_interval_s = poll_interval_s
        self._max_wait_s = max_wait_s
        self._release = redis.register_script(RELEASE_SCRIPT)
        self._renew = redis.register_script(RENEW_SCRIPT)

    def lock_key(self, key: str) -> str:
        return f"{self._prefix}:lock:{key}"

    async def try_acquire(self, key: str, ttl_s: float | None = None) -> Lease | None:
        ttl_s = ttl_s or self._default_ttl_s
        token = secrets.token_hex(16)
        acquired = await self._redis.set(self.lock_key(key), token, nx=True, px=int(ttl_s * 1000))
        return Lease(key=key, token=token, ttl_s=ttl_s) if acquired else None

    async def release(self, lease: Lease) -> bool:
        return bool(await self._release(keys=[self.lock_key(lease.key)], args=[lease.token]))

    async def renew(self, lease: Lease) -> bool:
        ttl_ms = int(lease.ttl_s * 1000)
        return bool(await self._renew(keys=[self.lock_key(lease.key)], args=[lease.token, ttl_ms]))

    @contextlib.asynccontextmanager
    async def acquire(
        self, key: str, ttl_s: float | None = None, wait_s: float | None = None
    ) -> AsyncIterator[Lease]:
        wait_s = self._max_wait_s if wait_s is None else wait_s
        loop = asyncio.get_running_loop()
        deadline = loop.time() + wait_s
        lease = await self.try_acquire(key, ttl_s)
        while lease is None:
            if loop.time() >= deadline:
                raise LockNotAcquiredError(f"lock '{key}' still held after {wait_s}s")
            await asyncio.sleep(self._poll_interval_s)
            lease = await self.try_acquire(key, ttl_s)
        renewer = asyncio.create_task(self._keep_alive(lease))
        try:
            yield lease
        finally:
            renewer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await renewer
            await self.release(lease)

    async def single_flight[T](
        self,
        key: str,
        fn: Callable[[], Awaitable[T]],
        *,
        skip_if: Callable[[], Awaitable[bool]] | None = None,
        ttl_s: float | None = None,
        wait_s: float | None = None,
    ) -> SingleFlightResult[T]:
        async with self.acquire(key, ttl_s=ttl_s, wait_s=wait_s):
            if skip_if is not None and await skip_if():
                return SingleFlightResult(ran=False, value=None)
            return SingleFlightResult(ran=True, value=await fn())

    async def _keep_alive(self, lease: Lease) -> None:
        while True:
            await asyncio.sleep(lease.ttl_s / 3)
            if not await self.renew(lease):
                return


class RateLimiter:
    def __init__(self, redis: Redis, *, prefix: str = "gh") -> None:
        self._redis = redis
        self._prefix = prefix
        self._script = redis.register_script(TOKEN_BUCKET_SCRIPT)

    async def try_take(self, name: str, *, rate_per_s: float, capacity: float, cost: float = 1) -> float:
        if cost > capacity:
            raise ValueError("cost cannot exceed bucket capacity")
        wait_ms = await self._script(keys=[f"{self._prefix}:rl:{name}"], args=[rate_per_s, capacity, cost])
        return int(wait_ms) / 1000

    async def take(self, name: str, *, rate_per_s: float, capacity: float, cost: float = 1) -> None:
        while True:
            wait_s = await self.try_take(name, rate_per_s=rate_per_s, capacity=capacity, cost=cost)
            if wait_s <= 0:
                return
            await asyncio.sleep(wait_s)


class ReservationStatus(IntEnum):
    reserved = 0
    exhausted = 1
    over_limit = 2
    over_client_cap = 3


@dataclass(frozen=True)
class Reservation:
    status: ReservationStatus
    used: int

    @property
    def ok(self) -> bool:
        return self.status is ReservationStatus.reserved


class QuotaCounter:
    """atomic per-day bucket counters; the db ledger is the audit trail that rebuilds them."""

    def __init__(
        self, redis: Redis, *, prefix: str = "gh", project: str = "default", ttl_s: int = 48 * 3600
    ) -> None:
        self._redis = redis
        self._prefix = prefix
        self._project = project
        self._ttl_s = ttl_s
        self._reserve = redis.register_script(QUOTA_RESERVE_SCRIPT)

    def bucket_key(self, bucket: str, day: date) -> str:
        return f"{self._prefix}:quota:{self._project}:{bucket}:{day.isoformat()}"

    def _client_key(self, bucket: str, day: date, client_id: str | None) -> str:
        return f"{self.bucket_key(bucket, day)}:client:{client_id or 'none'}"

    async def reserve(
        self,
        bucket: str,
        day: date,
        cost: int,
        *,
        limit: int,
        client_id: str | None = None,
        client_cap: int = 0,
    ) -> Reservation:
        if cost <= 0:
            raise ValueError("cost must be positive")
        base = self.bucket_key(bucket, day)
        keys = [base, self._client_key(bucket, day, client_id), f"{base}:exhausted"]
        track_client = "1" if client_id else "0"
        status, used = await self._reserve(
            keys=keys, args=[cost, limit, client_cap, self._ttl_s, track_client]
        )
        return Reservation(status=ReservationStatus(int(status)), used=int(used))

    async def refund(self, bucket: str, day: date, cost: int, *, client_id: str | None = None) -> None:
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.decrby(self.bucket_key(bucket, day), cost)
            if client_id:
                pipe.decrby(self._client_key(bucket, day, client_id), cost)
            await pipe.execute()

    async def mark_exhausted(self, bucket: str, day: date) -> None:
        await self._redis.set(f"{self.bucket_key(bucket, day)}:exhausted", "1", ex=self._ttl_s)

    async def used(self, bucket: str, day: date, *, client_id: str | None = None) -> int:
        key = self._client_key(bucket, day, client_id) if client_id else self.bucket_key(bucket, day)
        return int(await self._redis.get(key) or 0)

    async def rebuild(self, bucket: str, day: date, used: int) -> None:
        await self._redis.set(self.bucket_key(bucket, day), used, ex=self._ttl_s)
