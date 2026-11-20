import asyncio
import contextlib
import secrets
from collections.abc import AsyncIterator

from redis.asyncio import Redis

# a sorted set of lease tokens scored by expiry; expired leases are reclaimed on every acquire
ACQUIRE_SCRIPT = """
local now = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
local expires = tonumber(ARGV[3])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now)
if redis.call('ZCARD', KEYS[1]) < limit then
  redis.call('ZADD', KEYS[1], expires, ARGV[4])
  redis.call('PEXPIRE', KEYS[1], math.ceil(expires - now) + 1000)
  return 1
end
return 0
"""

RENEW_SCRIPT = """
if redis.call('ZSCORE', KEYS[1], ARGV[2]) then
  redis.call('ZADD', KEYS[1], 'XX', ARGV[1], ARGV[2])
  return 1
end
return 0
"""


class SemaphoreTimeout(TimeoutError):
    pass


class RedisSemaphore:
    """global concurrency limit across processes (sem:youtube, sem:llm:{model}, sem:crawl:{domain})."""

    def __init__(
        self,
        redis: Redis,
        name: str,
        limit: int,
        *,
        prefix: str = "gh",
        lease_s: float = 60,
        poll_s: float = 0.1,
    ) -> None:
        if limit < 1:
            raise ValueError("semaphore limit must be at least 1")
        self._redis = redis
        self._key = f"{prefix}:sem:{name}"
        self._limit = limit
        self._lease_ms = int(lease_s * 1000)
        self._poll_s = poll_s
        self._acquire = redis.register_script(ACQUIRE_SCRIPT)
        self._renew = redis.register_script(RENEW_SCRIPT)

    async def _now_ms(self) -> int:
        seconds, micros = await self._redis.time()
        return int(seconds) * 1000 + int(micros) // 1000

    async def try_acquire(self) -> str | None:
        token = secrets.token_hex(12)
        now = await self._now_ms()
        acquired = await self._acquire(keys=[self._key], args=[now, self._limit, now + self._lease_ms, token])
        return token if int(acquired) == 1 else None

    async def release(self, token: str) -> None:
        await self._redis.zrem(self._key, token)

    async def holders(self) -> int:
        now = await self._now_ms()
        await self._redis.zremrangebyscore(self._key, "-inf", now)
        return int(await self._redis.zcard(self._key))

    @contextlib.asynccontextmanager
    async def slot(self, wait_s: float | None = None) -> AsyncIterator[str]:
        loop = asyncio.get_running_loop()
        deadline = None if wait_s is None else loop.time() + wait_s
        token = await self.try_acquire()
        while token is None:
            if deadline is not None and loop.time() >= deadline:
                raise SemaphoreTimeout(f"{self._key} stayed full for {wait_s}s")
            await asyncio.sleep(self._poll_s)
            token = await self.try_acquire()
        keeper = asyncio.create_task(self._keep_alive(token))
        try:
            yield token
        finally:
            keeper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await keeper
            await self.release(token)

    async def _keep_alive(self, token: str) -> None:
        while True:
            await asyncio.sleep(self._lease_ms / 3000)
            now = await self._now_ms()
            if not int(await self._renew(keys=[self._key], args=[now + self._lease_ms, token])):
                return
