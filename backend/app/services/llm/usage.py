import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Literal, Protocol

from sqlalchemy import insert

from app.core.db import SessionFactory
from app.core.logging import get_logger
from app.models.ledgers import ProviderUsageLedger

logger = get_logger(__name__)
AttemptKind = Literal["initial", "retry", "repair", "fallback", "cache"]


@dataclass(frozen=True, slots=True)
class AttemptRecord:
    """one provider attempt; holds hashes and counts only, never prompt or output text."""

    occurred_at: datetime
    provider: str
    operation: str
    stage: str | None
    tier: str | None
    prompt_id: str
    prompt_version: str
    schema_name: str | None
    model_requested: str
    model_resolved: str | None
    route_index: int
    attempt_no: int
    attempt_kind: AttemptKind
    outcome: Literal["ok", "error"]
    error_kind: str | None
    status_code: int | None
    stop_reason: str | None
    refusal_category: str | None
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    thinking_tokens: int | None
    cost_usd: float | None
    latency_ms: int
    provider_request_id: str | None
    input_hash: str
    output_hash: str | None
    run_id: uuid.UUID | None
    client_id: uuid.UUID | None
    degraded_route: bool = False
    cache_hit: bool = False
    request_id: uuid.UUID = field(default_factory=uuid.uuid4)


class UsageRecorder(Protocol):
    async def record(self, attempts: list[AttemptRecord]) -> None: ...


class MemoryUsageRecorder:
    def __init__(self) -> None:
        self.attempts: list[AttemptRecord] = []

    async def record(self, attempts: list[AttemptRecord]) -> None:
        self.attempts.extend(attempts)


class SqlUsageRecorder:
    """writes per-attempt rows to the unified provider_usage_ledger (99 C82)."""

    def __init__(self, session_factory: SessionFactory, *, scope: str = "client") -> None:
        self._session_factory = session_factory
        self._scope = scope

    async def record(self, attempts: list[AttemptRecord]) -> None:
        if not attempts:
            return
        rows = []
        for attempt in attempts:
            row = asdict(attempt)
            cost = row.pop("cost_usd")
            rows.append(
                {
                    **row,
                    "provider_kind": "llm",
                    "scope": self._scope if attempt.client_id is not None else "shared",
                    "cost_usd": None if cost is None else Decimal(f"{cost:.6f}"),
                    "price_missing": cost is None,
                }
            )
        async with self._session_factory() as session:
            await session.execute(insert(ProviderUsageLedger), rows)
            await session.commit()


class SafeUsageRecorder:
    # accounting must never fail a call that already succeeded
    def __init__(self, inner: UsageRecorder) -> None:
        self._inner = inner

    async def record(self, attempts: list[AttemptRecord]) -> None:
        try:
            await self._inner.record(attempts)
        except Exception:
            logger.exception("llm_usage_record_failed", attempts=len(attempts))
