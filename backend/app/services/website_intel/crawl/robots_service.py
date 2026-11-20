import json
from typing import Protocol

from redis.asyncio import Redis

from app.domain.web import UserAgentMode
from app.ports.crawler import FetchContext
from app.services.website_intel.crawl.fetching import RawFetcher
from app.services.website_intel.crawl.robots import RobotsRules, robots_from_response
from app.services.website_intel.inputs.urls import host_of, origin_of


class RobotsCache(Protocol):
    async def get(self, host: str) -> dict[str, object] | None: ...

    async def put(self, host: str, value: dict[str, object], ttl_s: int) -> None: ...


class RedisRobotsCache:
    # shared across clients and keyed by host (rfc 9309 §2.4 caps the age at 24h)
    def __init__(self, redis: Redis, *, prefix: str) -> None:
        self._redis = redis
        self._prefix = f"{prefix}:robots"

    async def get(self, host: str) -> dict[str, object] | None:
        raw = await self._redis.get(f"{self._prefix}:{host}")
        return json.loads(raw) if raw else None

    async def put(self, host: str, value: dict[str, object], ttl_s: int) -> None:
        await self._redis.set(f"{self._prefix}:{host}", json.dumps(value), ex=ttl_s)


class RobotsService:
    """fetches robots.txt with the honest agent, retries once with the compat agent on 401/403."""

    def __init__(
        self,
        fetcher: RawFetcher,
        *,
        token: str,
        cache: RobotsCache | None = None,
        ttl_s: int = 86_400,
    ) -> None:
        self._fetcher = fetcher
        self._token = token
        self._cache = cache
        self._ttl_s = ttl_s
        self._memo: dict[str, tuple[RobotsRules, UserAgentMode]] = {}

    async def rules(self, url: str, ctx: FetchContext) -> tuple[RobotsRules, UserAgentMode]:
        host = host_of(url)
        if host in self._memo:
            return self._memo[host]
        cached = await self._cache.get(host) if self._cache is not None else None
        if cached is not None:
            status = cached.get("status_code")
            body = cached.get("body")
            result = (
                robots_from_response(
                    status if isinstance(status, int) else None,
                    body if isinstance(body, str) else None,
                    token=self._token,
                ),
                UserAgentMode(str(cached.get("ua_mode", UserAgentMode.honest.value))),
            )
            self._memo[host] = result
            return result
        robots_url = origin_of(url) + "/robots.txt"
        mode = ctx.ua_mode
        raw = await self._fetcher.fetch_raw(robots_url, ctx.model_copy(update={"ua_mode": mode}))
        if raw.status_code in {401, 403} and mode is UserAgentMode.honest:
            mode = UserAgentMode.compat
            raw = await self._fetcher.fetch_raw(robots_url, ctx.model_copy(update={"ua_mode": mode}))
        body = raw.decoded() if raw.body else None
        rules = robots_from_response(raw.status_code, body, token=self._token)
        if self._cache is not None and raw.status_code is not None and raw.status_code < 500:
            await self._cache.put(
                host, {"status_code": raw.status_code, "body": body, "ua_mode": mode.value}, self._ttl_s
            )
        self._memo[host] = (rules, mode)
        return rules, mode
