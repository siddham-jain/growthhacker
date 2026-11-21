"""read-only views over the ledgers other components own (02 quota, 10/03/08 provider usage, 11 runs)."""

from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import Integer, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ledgers import ProviderUsageLedger, QuotaCacheHitsDaily, YtQuotaLedger
from app.models.runs import PipelineRun, StageRun

TERMINAL_RUN_STATUSES = ("succeeded", "partial", "failed", "cancelled", "superseded")
COMPLETED_RUN_STATUSES = ("succeeded", "partial")
TIMED_STAGE_STATUSES = ("succeeded", "degraded")
COUNTED_STAGE_STATUSES = ("succeeded", "degraded", "failed")


@dataclass(frozen=True)
class QuotaRow:
    day_pt: date
    bucket: str
    endpoint: str
    client_id: UUID | None
    run_id: UUID | None
    run_kind: str | None
    units: int
    calls: int
    errors: int


@dataclass(frozen=True)
class CacheHitRow:
    day_pt: date
    client_id: UUID
    hits: int


@dataclass(frozen=True)
class RunRow:
    run_id: UUID
    workspace_id: UUID
    client_id: UUID
    kind: str
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    queued_ms: int | None

    @property
    def wall_ms(self) -> int | None:
        if self.started_at is None or self.finished_at is None:
            return None
        return int((self.finished_at - self.started_at).total_seconds() * 1000)


@dataclass(frozen=True)
class StageRow:
    client_id: UUID
    run_kind: str
    stage: str
    status: str
    duration_ms: int | None
    run_id: UUID | None = None
    finished_at: datetime | None = None
    counts: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderRow:
    provider_kind: str
    provider: str
    operation: str
    model: str | None
    client_id: UUID | None
    run_id: UUID | None
    run_kind: str | None
    outcome: str
    error_kind: str | None
    calls: int
    retries: int
    price_missing: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    usd: float
    credits: float
    pages: int


class QuotaSource(Protocol):
    async def rows(self, session: AsyncSession, day_from: date, day_to: date) -> list[QuotaRow]: ...

    async def cache_hits(self, session: AsyncSession, day_from: date, day_to: date) -> list[CacheHitRow]: ...


class RunSource(Protocol):
    async def runs(self, session: AsyncSession, start: datetime, end: datetime) -> list[RunRow]: ...

    async def stages(self, session: AsyncSession, start: datetime, end: datetime) -> list[StageRow]: ...


class ProviderUsageSource(Protocol):
    async def rows(
        self, session: AsyncSession, start: datetime, end: datetime, kinds: Collection[str]
    ) -> list[ProviderRow]: ...

    async def latency_p95(
        self, session: AsyncSession, start: datetime, end: datetime, kind: str
    ) -> int | None: ...


class YtQuotaLedgerSource:
    """yt_quota_ledger grouped per pt day; quota days follow google's pacific-time reset."""

    async def rows(self, session: AsyncSession, day_from: date, day_to: date) -> list[QuotaRow]:
        ledger = YtQuotaLedger
        statement = (
            select(
                ledger.pt_day,
                ledger.bucket,
                ledger.endpoint,
                ledger.client_id,
                ledger.run_id,
                PipelineRun.kind,
                func.sum(ledger.units),
                func.count(),
                func.sum(case((ledger.http_status >= 400, 1), else_=0)),
            )
            .outerjoin(PipelineRun, PipelineRun.id == ledger.run_id)
            .where(ledger.pt_day >= day_from, ledger.pt_day <= day_to)
            .group_by(
                ledger.pt_day,
                ledger.bucket,
                ledger.endpoint,
                ledger.client_id,
                ledger.run_id,
                PipelineRun.kind,
            )
        )
        return [
            QuotaRow(day, bucket, endpoint, client, run, kind, int(units or 0), int(calls), int(errors or 0))
            for day, bucket, endpoint, client, run, kind, units, calls, errors in await session.execute(
                statement
            )
        ]

    async def cache_hits(self, session: AsyncSession, day_from: date, day_to: date) -> list[CacheHitRow]:
        hits = QuotaCacheHitsDaily
        statement = (
            select(hits.pt_day, hits.client_id, func.sum(hits.hits))
            .where(hits.pt_day >= day_from, hits.pt_day <= day_to)
            .group_by(hits.pt_day, hits.client_id)
        )
        return [
            CacheHitRow(day, client, int(total or 0))
            for day, client, total in await session.execute(statement)
        ]


class PipelineRunSource:
    """runs that started inside the period; wall time excludes queue wait (12's definition, 99 c69)."""

    async def runs(self, session: AsyncSession, start: datetime, end: datetime) -> list[RunRow]:
        run = PipelineRun
        statement = select(
            run.id,
            run.workspace_id,
            run.client_id,
            run.kind,
            run.status,
            run.started_at,
            run.finished_at,
            run.timings["queued_ms"].astext.cast(Integer),
        ).where(run.started_at >= start, run.started_at < end)
        return [RunRow(*row) for row in await session.execute(statement)]

    async def stages(self, session: AsyncSession, start: datetime, end: datetime) -> list[StageRow]:
        statement = (
            select(
                PipelineRun.client_id,
                PipelineRun.kind,
                StageRun.stage,
                StageRun.status,
                StageRun.duration_ms,
                StageRun.run_id,
                StageRun.finished_at,
                StageRun.counts,
            )
            .join(PipelineRun, PipelineRun.id == StageRun.run_id)
            .where(
                PipelineRun.started_at >= start,
                PipelineRun.started_at < end,
                StageRun.status.in_(COUNTED_STAGE_STATUSES),
            )
        )
        return [StageRow(*row) for row in await session.execute(statement)]


class ProviderUsageLedgerSource:
    """provider_usage_ledger (llm, tavily, crawl); one row per attempt, so retries cost what they cost."""

    async def rows(
        self, session: AsyncSession, start: datetime, end: datetime, kinds: Collection[str]
    ) -> list[ProviderRow]:
        ledger = ProviderUsageLedger
        model = func.coalesce(ledger.model_resolved, ledger.model_requested)
        statement = (
            select(
                ledger.provider_kind,
                ledger.provider,
                ledger.operation,
                model,
                ledger.client_id,
                ledger.run_id,
                PipelineRun.kind,
                ledger.outcome,
                ledger.error_kind,
                func.count(),
                func.sum(case((ledger.attempt_no > 1, 1), else_=0)),
                func.sum(case((ledger.price_missing, 1), else_=0)),
                func.sum(func.coalesce(ledger.input_tokens, 0)),
                func.sum(func.coalesce(ledger.output_tokens, 0)),
                func.sum(func.coalesce(ledger.cache_read_tokens, 0)),
                func.sum(func.coalesce(ledger.cache_write_tokens, 0)),
                func.sum(func.coalesce(ledger.cost_usd, 0)),
                func.sum(func.coalesce(ledger.credits, 0)),
                func.sum(func.coalesce(ledger.pages, 0)),
            )
            .outerjoin(PipelineRun, PipelineRun.id == ledger.run_id)
            .where(ledger.occurred_at >= start, ledger.occurred_at < end, ledger.provider_kind.in_(kinds))
            .group_by(
                ledger.provider_kind,
                ledger.provider,
                ledger.operation,
                model,
                ledger.client_id,
                ledger.run_id,
                PipelineRun.kind,
                ledger.outcome,
                ledger.error_kind,
            )
        )
        rows = []
        for row in await session.execute(statement):
            (*keys, calls, retries, missing, tin, tout, tread, twrite, usd, credits, pages) = row
            rows.append(
                ProviderRow(
                    *keys,
                    calls=int(calls),
                    retries=int(retries or 0),
                    price_missing=int(missing or 0),
                    input_tokens=int(tin or 0),
                    output_tokens=int(tout or 0),
                    cache_read_tokens=int(tread or 0),
                    cache_write_tokens=int(twrite or 0),
                    usd=float(usd or 0),
                    credits=float(credits or 0),
                    pages=int(pages or 0),
                )
            )
        return rows

    async def latency_p95(
        self, session: AsyncSession, start: datetime, end: datetime, kind: str
    ) -> int | None:
        ledger = ProviderUsageLedger
        statement = select(func.percentile_cont(0.95).within_group(ledger.latency_ms)).where(
            ledger.occurred_at >= start,
            ledger.occurred_at < end,
            ledger.provider_kind == kind,
            ledger.latency_ms.is_not(None),
        )
        value = (await session.execute(statement)).scalar_one_or_none()
        return None if value is None else int(value)


@dataclass(frozen=True)
class OpsSources:
    quota: QuotaSource
    runs: RunSource
    providers: ProviderUsageSource

    @classmethod
    def default(cls) -> "OpsSources":
        return cls(
            quota=YtQuotaLedgerSource(), runs=PipelineRunSource(), providers=ProviderUsageLedgerSource()
        )
