import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from app.core.db import SessionFactory
from app.models.ledgers import ProviderUsageLedger
from app.ports.clock import Clock


@dataclass(frozen=True)
class CreditCharge:
    operation: str
    credits: float
    pages: int
    latency_ms: int
    outcome: str
    cache_hit: bool = False
    error_kind: str | None = None


class UsageRecorder(Protocol):
    async def record(self, charge: CreditCharge, *, client_id: UUID | None, run_id: UUID | None) -> None: ...


@dataclass
class CreditMeter:
    # running totals for one attribution scope (a run or a test)
    spent: float = 0.0
    by_operation: dict[str, float] = field(default_factory=dict)
    calls: int = 0

    def add(self, charge: CreditCharge) -> None:
        self.calls += 1
        self.spent += charge.credits
        self.by_operation[charge.operation] = self.by_operation.get(charge.operation, 0.0) + charge.credits


class PgUsageRecorder:
    """writes one provider_usage_ledger row per tavily call (hashes and counts only)."""

    def __init__(
        self, session_factory: SessionFactory, clock: Clock, *, stage: str = "website_intel"
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock
        self._stage = stage

    async def record(self, charge: CreditCharge, *, client_id: UUID | None, run_id: UUID | None) -> None:
        async with self._session_factory() as session:
            session.add(
                ProviderUsageLedger(
                    request_id=uuid.uuid4(),
                    occurred_at=self._clock.now(),
                    provider_kind="tavily",
                    provider="tavily",
                    operation=charge.operation,
                    stage=self._stage,
                    outcome=charge.outcome,
                    error_kind=charge.error_kind,
                    credits=Decimal(str(round(charge.credits, 2))),
                    pages=charge.pages,
                    latency_ms=charge.latency_ms,
                    cache_hit=charge.cache_hit,
                    client_id=client_id,
                    run_id=run_id,
                )
            )
            await session.commit()
