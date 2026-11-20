import asyncio
import uuid
from collections.abc import Callable
from datetime import date
from typing import Any
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy.dialects.postgresql import insert

from app.core.clock import Clock
from app.core.db import UnitOfWork
from app.core.logging import get_logger
from app.domain.youtube import QuotaUsage
from app.models.ledgers import QuotaCacheHitsDaily
from app.repositories.shared import QuotaLedgerRepository
from app.services.youtube.quota import PacificDay

logger = get_logger(__name__)


class QuotaLedger:
    """buffers billable-call rows and writes them in batches; redis counters stay authoritative."""

    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        clock: Clock,
        *,
        project: str = "default",
        timezone: str = "America/Los_Angeles",
        flush_at: int = 50,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._project = project
        self._pacific = PacificDay(timezone)
        self._flush_at = flush_at
        self._buffer: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()

    def row(self, usage: QuotaUsage, *, cache_key: str | None = None) -> dict[str, Any]:
        now = self._clock.now()
        return {
            "request_id": uuid.uuid4(),
            "occurred_at": now,
            "pt_day": self._pacific.day(now),
            "project": self._project,
            "bucket": usage.bucket.value,
            "endpoint": usage.endpoint,
            "units": usage.units,
            "scope": usage.scope,
            "priority": usage.priority.value,
            "cache_outcome": usage.cache_outcome,
            "http_status": usage.http_status,
            "reason": usage.reason,
            "cache_key": cache_key,
            "client_id": usage.client_id,
            "run_id": usage.run_id,
        }

    async def record(self, usage: QuotaUsage, *, cache_key: str | None = None) -> None:
        self._buffer.append(self.row(usage, cache_key=cache_key))
        if len(self._buffer) >= self._flush_at:
            await self.flush()

    async def flush(self) -> int:
        async with self._lock:
            rows, self._buffer = self._buffer, []
            if not rows:
                return 0
            try:
                async with self._uow_factory() as uow:
                    written = await uow.repository(QuotaLedgerRepository).record(rows)
                    await uow.commit()
                return written
            except Exception:
                # keep the rows for the next flush; the fetch itself already succeeded
                self._buffer = rows + self._buffer
                logger.exception("quota_ledger_flush_failed", rows=len(rows))
                raise


class CacheHitCounter:
    """cache hits cost 0 units; counted per client per pacific day and flushed to postgres."""

    def __init__(
        self, redis: Redis, clock: Clock, *, prefix: str = "gh", timezone: str = "America/Los_Angeles"
    ) -> None:
        self._redis = redis
        self._clock = clock
        self._prefix = prefix
        self._pacific = PacificDay(timezone)

    def _key(self, day: date) -> str:
        return f"{self._prefix}:yt_cache_hits:{day.isoformat()}"

    async def hit(self, client_id: UUID | None, data_kind: str, count: int = 1) -> None:
        if client_id is None or count <= 0:
            return
        key = self._key(self._pacific.day(self._clock.now()))
        await self._redis.hincrby(key, f"{client_id}|{data_kind}", count)
        await self._redis.expire(key, 3 * 24 * 3600)

    async def flush(self, day: date, uow_factory: Callable[[], UnitOfWork]) -> int:
        key = self._key(day)
        counts = await self._redis.hgetall(key)
        if not counts:
            return 0
        rows = []
        for field, hits in counts.items():
            client_id, data_kind = field.split("|", 1)
            rows.append(
                {"pt_day": day, "client_id": UUID(client_id), "data_kind": data_kind, "hits": int(hits)}
            )
        statement = insert(QuotaCacheHitsDaily).values(rows)
        statement = statement.on_conflict_do_update(
            index_elements=["pt_day", "client_id", "data_kind"], set_={"hits": statement.excluded.hits}
        )
        async with uow_factory() as uow:
            await uow.session.execute(statement)
            await uow.commit()
        return len(rows)
