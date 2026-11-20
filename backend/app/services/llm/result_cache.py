import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.core.db import SessionFactory
from app.core.logging import get_logger
from app.models.llm import LLMResultCache
from app.ports.clock import Clock

logger = get_logger(__name__)
# derived from youtube api data, so never kept past the retention horizon
MAX_TTL_DAYS = 28


def cache_key(task: str, prompt_version: str, canonical_input: str, language: str, schema_name: str) -> str:
    material = "\x1f".join((task, prompt_version, schema_name, language, canonical_input))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


@dataclass(frozen=True, slots=True)
class CachedResult:
    output: Any
    model: str
    prompt_version: str


class ResultCache(Protocol):
    async def get(self, key: str) -> CachedResult | None: ...

    async def put(
        self, key: str, *, task: str, prompt_version: str, output: Any, model: str, ttl_days: int
    ) -> None: ...


class MemoryResultCache:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._entries: dict[str, tuple[CachedResult, datetime]] = {}

    async def get(self, key: str) -> CachedResult | None:
        entry = self._entries.get(key)
        if entry is None or entry[1] <= self._clock.now():
            return None
        return entry[0]

    async def put(
        self, key: str, *, task: str, prompt_version: str, output: Any, model: str, ttl_days: int
    ) -> None:
        if ttl_days <= 0:
            return
        expires = self._clock.now() + timedelta(days=min(ttl_days, MAX_TTL_DAYS))
        self._entries[key] = (
            CachedResult(output=output, model=model, prompt_version=prompt_version),
            expires,
        )


class SqlResultCache:
    """postgres llm_result_cache; rows expire through expires_at and the retention sweep."""

    def __init__(self, session_factory: SessionFactory, clock: Clock) -> None:
        self._session_factory = session_factory
        self._clock = clock

    async def get(self, key: str) -> CachedResult | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(LLMResultCache).where(
                    LLMResultCache.key == key, LLMResultCache.expires_at > self._clock.now()
                )
            )
        if row is None:
            return None
        return CachedResult(output=row.output["value"], model=row.model, prompt_version=row.prompt_version)

    async def put(
        self, key: str, *, task: str, prompt_version: str, output: Any, model: str, ttl_days: int
    ) -> None:
        if ttl_days <= 0:
            return
        now = self._clock.now()
        values = {
            "key": key,
            "task": task,
            "prompt_version": prompt_version,
            "output": {"value": output},
            "model": model,
            "created_at": now,
            "expires_at": now + timedelta(days=min(ttl_days, MAX_TTL_DAYS)),
        }
        statement = insert(LLMResultCache).values(values)
        statement = statement.on_conflict_do_update(
            index_elements=[LLMResultCache.key],
            set_={name: statement.excluded[name] for name in values if name != "key"},
        )
        async with self._session_factory() as session:
            await session.execute(statement)
            await session.commit()


class SafeResultCache:
    # a cache outage costs money, never correctness
    def __init__(self, inner: ResultCache) -> None:
        self._inner = inner

    async def get(self, key: str) -> CachedResult | None:
        try:
            return await self._inner.get(key)
        except Exception:
            logger.exception("llm_result_cache_get_failed")
            return None

    async def put(
        self, key: str, *, task: str, prompt_version: str, output: Any, model: str, ttl_days: int
    ) -> None:
        try:
            await self._inner.put(
                key, task=task, prompt_version=prompt_version, output=output, model=model, ttl_days=ttl_days
            )
        except Exception:
            logger.exception("llm_result_cache_put_failed")
