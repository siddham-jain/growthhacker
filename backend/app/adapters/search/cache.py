import hashlib
import json
from typing import Any, Protocol

from redis.asyncio import Redis


class ResponseCache(Protocol):
    async def get(self, key: str) -> dict[str, Any] | None: ...

    async def put(self, key: str, value: dict[str, Any], ttl_s: int) -> None: ...


def cache_key(operation: str, params: dict[str, Any]) -> str:
    digest = hashlib.sha256(json.dumps(params, sort_keys=True, default=str).encode()).hexdigest()[:32]
    return f"{operation}:{digest}"


class RedisResponseCache:
    def __init__(self, redis: Redis, *, prefix: str) -> None:
        self._redis = redis
        self._prefix = f"{prefix}:websearch"

    async def get(self, key: str) -> dict[str, Any] | None:
        raw = await self._redis.get(f"{self._prefix}:{key}")
        return json.loads(raw) if raw else None

    async def put(self, key: str, value: dict[str, Any], ttl_s: int) -> None:
        await self._redis.set(f"{self._prefix}:{key}", json.dumps(value, default=str), ex=ttl_s)
