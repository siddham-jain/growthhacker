import math
import time
from dataclasses import dataclass

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import ApiRateLimitSettings
from app.core.logging import get_logger

logger = get_logger(__name__)

# token bucket: refills limit tokens per window; returns allowed, remaining, seconds until full
BUCKET_SCRIPT = """
local limit = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
local clock = redis.call('TIME')
local now = tonumber(clock[1]) * 1000 + math.floor(tonumber(clock[2]) / 1000)
local state = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(state[1]) or limit
local ts = tonumber(state[2]) or now
tokens = math.min(limit, tokens + (now - ts) * limit / window_ms)
local allowed = 0
if tokens >= 1 then
  tokens = tokens - 1
  allowed = 1
end
redis.call('HSET', KEYS[1], 'tokens', tostring(tokens), 'ts', now)
redis.call('PEXPIRE', KEYS[1], window_ms + 1000)
return {allowed, math.floor(tokens), math.ceil((limit - tokens) * window_ms / limit)}
"""


@dataclass(frozen=True)
class Bucket:
    name: str
    limit: int
    window_s: int


@dataclass(frozen=True)
class RateDecision:
    bucket: Bucket
    allowed: bool
    remaining: int
    reset_s: int

    @property
    def retry_after_s(self) -> int:
        return max(1, math.ceil(self.bucket.window_s / self.bucket.limit))

    def policy_header(self) -> str:
        return f'"{self.bucket.name}";q={self.bucket.limit};w={self.bucket.window_s}'

    def header(self) -> str:
        return f'"{self.bucket.name}";r={self.remaining};t={self.reset_s}'


def buckets(settings: ApiRateLimitSettings) -> dict[str, Bucket]:
    return {
        "read": Bucket("read", settings.read_per_min, 60),
        "write": Bucket("write", settings.write_per_min, 60),
        "run_start_client": Bucket("run_start_client", settings.run_start_per_hour_client, 3600),
        "run_start_workspace": Bucket("run_start_workspace", settings.run_start_per_hour_workspace, 3600),
        "sse_open": Bucket("sse_open", settings.sse_open_per_min, 60),
        "oauth": Bucket("oauth", settings.oauth_per_min, 60),
    }


class _LocalBuckets:
    """per-process fallback while redis is down: fail safe at a reduced limit, never fail open."""

    def __init__(self, share: float) -> None:
        self._share = share
        self._state: dict[str, tuple[float, float]] = {}

    def hit(self, key: str, bucket: Bucket) -> RateDecision:
        limit = max(1, int(bucket.limit * self._share))
        now = time.monotonic()
        tokens, ts = self._state.get(key, (float(limit), now))
        tokens = min(limit, tokens + (now - ts) * limit / bucket.window_s)
        allowed = tokens >= 1
        if allowed:
            tokens -= 1
        self._state[key] = (tokens, now)
        reset = math.ceil((limit - tokens) * bucket.window_s / limit)
        return RateDecision(Bucket(bucket.name, limit, bucket.window_s), allowed, int(tokens), reset)


class ApiRateLimiter:
    def __init__(self, redis: Redis, *, prefix: str, settings: ApiRateLimitSettings) -> None:
        self._redis = redis
        self._prefix = prefix
        self._script = redis.register_script(BUCKET_SCRIPT)
        self._local = _LocalBuckets(settings.local_fallback_share)
        self.buckets = buckets(settings)

    async def hit(self, subject: str, bucket_name: str) -> RateDecision:
        bucket = self.buckets[bucket_name]
        key = f"{self._prefix}:ratelimit:{bucket.name}:{subject}"
        try:
            allowed, remaining, reset_ms = await self._script(
                keys=[key], args=[bucket.limit, bucket.window_s * 1000]
            )
        except (RedisError, OSError) as error:
            logger.warning("ratelimit.redis_unavailable", error=type(error).__name__)
            return self._local.hit(key, bucket)
        return RateDecision(bucket, bool(int(allowed)), int(remaining), math.ceil(int(reset_ms) / 1000))
