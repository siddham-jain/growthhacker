import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import insert

from app.core.db import SessionFactory
from app.core.tenancy import TenantScope
from app.models.ledgers import ProviderUsageLedger, QuotaCacheHitsDaily, YtQuotaLedger
from app.models.runs import PipelineRun, StageRun
from tests.foundation.factories import create_tenant, insert_row

NOW = datetime(2026, 11, 21, 20, tzinfo=UTC)
PT_DAY = date(2026, 11, 21)


async def tenant(session_factory: SessionFactory, label: str) -> TenantScope:
    async with session_factory() as session, session.begin():
        return await create_tenant(session, f"{label}-{uuid.uuid4().hex[:6]}")


async def run(
    session_factory: SessionFactory,
    scope: TenantScope,
    *,
    kind: str = "daily_refresh",
    status: str = "succeeded",
    started_at: datetime = NOW - timedelta(hours=2),
    wall_s: float = 60.0,
    queued_ms: int | None = 500,
) -> uuid.UUID:
    values: dict[str, Any] = {
        "kind": kind,
        "trigger": "schedule",
        "status": status,
        "started_at": started_at,
        "finished_at": started_at + timedelta(seconds=wall_s),
        "timings": {} if queued_ms is None else {"queued_ms": queued_ms},
        "config_hash": "h",
        "queue": "gh:batch",
    }
    async with session_factory() as session, session.begin():
        return (await insert_row(session, PipelineRun.__table__, scope, values))["id"]


async def stage(
    session_factory: SessionFactory,
    scope: TenantScope,
    run_id: uuid.UUID,
    name: str,
    ms: int,
    status: str = "succeeded",
) -> None:
    async with session_factory() as session, session.begin():
        await insert_row(
            session,
            StageRun.__table__,
            scope,
            {"run_id": run_id, "stage": name, "status": status, "duration_ms": ms},
        )


async def quota(
    session_factory: SessionFactory,
    units: int,
    *,
    bucket: str = "main",
    client_id: uuid.UUID | None = None,
    run_id: uuid.UUID | None = None,
    day: date = PT_DAY,
    endpoint: str = "videos.list",
    http_status: int = 200,
) -> None:
    async with session_factory() as session, session.begin():
        await session.execute(
            insert(YtQuotaLedger).values(
                request_id=uuid.uuid4(),
                occurred_at=NOW - timedelta(hours=1),
                pt_day=day,
                bucket=bucket,
                endpoint=endpoint,
                units=units,
                scope="client" if client_id else "shared",
                http_status=http_status,
                client_id=client_id,
                run_id=run_id,
            )
        )


async def cache_hits(
    session_factory: SessionFactory, client_id: uuid.UUID, hits: int, day: date = PT_DAY
) -> None:
    async with session_factory() as session, session.begin():
        await session.execute(
            insert(QuotaCacheHitsDaily).values(pt_day=day, client_id=client_id, data_kind="video", hits=hits)
        )


async def provider(
    session_factory: SessionFactory,
    kind: str,
    *,
    client_id: uuid.UUID | None = None,
    run_id: uuid.UUID | None = None,
    usd: float | None = None,
    credits: float | None = None,
    outcome: str = "ok",
    attempt_no: int = 1,
    model: str | None = None,
    occurred_at: datetime = NOW - timedelta(hours=1),
) -> None:
    async with session_factory() as session, session.begin():
        await session.execute(
            insert(ProviderUsageLedger).values(
                request_id=uuid.uuid4(),
                occurred_at=occurred_at,
                provider_kind=kind,
                provider="anthropic" if kind == "llm" else kind,
                operation="card_write" if kind == "llm" else "search",
                model_resolved=model,
                outcome=outcome,
                attempt_no=attempt_no,
                input_tokens=1000 if kind == "llm" else None,
                output_tokens=200 if kind == "llm" else None,
                cost_usd=None if usd is None else Decimal(str(usd)),
                credits=None if credits is None else Decimal(str(credits)),
                latency_ms=100,
                client_id=client_id,
                run_id=run_id,
            )
        )
