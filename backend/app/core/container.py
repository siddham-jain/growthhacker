from functools import cached_property
from typing import Any

import httpx
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.clock import Clock, SystemClock
from app.core.config import Settings
from app.core.db import SessionFactory, UnitOfWork, build_engine, build_session_factory
from app.core.redis import LockManager, QuotaCounter, RateLimiter, build_redis
from app.core.registry import PortRegistry, adapter_registry
from app.core.security import PasswordService, SecretCipher


class Container:
    """composition root; concrete adapters come from adapter_registry by settings.adapters key, lazily."""

    def __init__(self, settings: Settings, clock: Clock, adapters: PortRegistry) -> None:
        self.settings = settings
        self.clock = clock
        self._adapters = adapters
        self._overrides: dict[str, Any] = {}
        self._built: dict[str, Any] = {}

    @cached_property
    def engine(self) -> AsyncEngine:
        return build_engine(self.settings.db)

    @cached_property
    def session_factory(self) -> SessionFactory:
        return build_session_factory(self.engine)

    @cached_property
    def redis(self) -> Redis:
        return build_redis(self.settings.redis)

    @cached_property
    def http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=5.0),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
            follow_redirects=False,
        )

    @cached_property
    def lock_manager(self) -> LockManager:
        redis_settings = self.settings.redis
        return LockManager(
            self.redis,
            prefix=redis_settings.key_prefix,
            default_ttl_s=redis_settings.lock_default_ttl_s,
            poll_interval_s=redis_settings.lock_poll_interval_s,
            max_wait_s=redis_settings.lock_max_wait_s,
        )

    @cached_property
    def rate_limiter(self) -> RateLimiter:
        return RateLimiter(self.redis, prefix=self.settings.redis.key_prefix)

    @cached_property
    def quota_counter(self) -> QuotaCounter:
        quota = self.settings.quota
        return QuotaCounter(
            self.redis,
            prefix=self.settings.redis.key_prefix,
            project=quota.project,
            ttl_s=quota.counter_ttl_s,
        )

    @cached_property
    def cipher(self) -> SecretCipher:
        return SecretCipher(self.settings.security.token_encryption_keys)

    @cached_property
    def passwords(self) -> PasswordService:
        return PasswordService()

    def uow(self, *, read_only: bool = False) -> UnitOfWork:
        return UnitOfWork(self.session_factory, read_only=read_only)

    def override(self, port_name: str, implementation: Any) -> None:
        self._overrides[port_name] = implementation
        self._built.pop(port_name, None)

    def adapter(self, port_name: str) -> Any:
        if port_name in self._overrides:
            return self._overrides[port_name]
        if port_name not in self._built:
            provider = getattr(self.settings.adapters, port_name, None)
            if provider is None:
                raise LookupError(f"settings.adapters has no provider configured for port '{port_name}'")
            factory = self._adapters.get(port_name, provider)
            self._built[port_name] = factory(self)
        return self._built[port_name]

    async def aclose(self) -> None:
        for adapter in self._built.values():
            close = getattr(adapter, "aclose", None)
            if close is not None:
                await close()
        self._built.clear()
        if "http" in self.__dict__:
            await self.http.aclose()
        if "redis" in self.__dict__:
            await self.redis.aclose()
        if "engine" in self.__dict__:
            await self.engine.dispose()


def build_container(
    settings: Settings, clock: Clock | None = None, *, adapters: PortRegistry = adapter_registry
) -> Container:
    return Container(settings=settings, clock=clock or SystemClock(), adapters=adapters)
