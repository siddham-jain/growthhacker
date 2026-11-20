"""OpsService for 11's /api/v1/ops routes; quota totals are project-wide, per-client rows are workspace-scoped."""

from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app.api import ids
from app.api.schemas.admin import ClientQuota, LLMUsage, ModelUsage, OpsQuotaOut, OwnerReport, YouTubeQuota
from app.api.services import OPS, api_service_registry
from app.core.clock import Clock
from app.core.config import Settings
from app.core.db import SessionFactory
from app.core.tenancy import TenantScope
from app.evaluation.ops.report import OpsReportBuilder
from app.models.ledgers import ProviderUsageLedger, YtQuotaLedger
from app.models.tenancy import Client

DEFAULT_REPORT_DAYS = 7


class LedgerOpsService:
    def __init__(self, session_factory: SessionFactory, settings: Settings, clock: Clock) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.clock = clock
        self.tz = ZoneInfo(settings.quota.timezone)

    async def _client_ids(self, scope: TenantScope) -> list[UUID]:
        statement = select(Client.id).where(
            Client.workspace_id == scope.workspace_id, Client.deleted_at.is_(None)
        )
        async with self.session_factory() as session:
            return list((await session.execute(statement)).scalars())

    async def quota(self, scope: TenantScope, day: date | None) -> OpsQuotaOut:
        pt_day = day or self.clock.now().astimezone(self.tz).date()
        client_ids = await self._client_ids(scope)
        ledger = YtQuotaLedger
        async with self.session_factory() as session:
            totals = dict(
                (
                    await session.execute(
                        select(ledger.bucket, func.sum(ledger.units))
                        .where(ledger.pt_day == pt_day)
                        .group_by(ledger.bucket)
                    )
                ).all()
            )
            per_client = (
                await session.execute(
                    select(ledger.client_id, ledger.bucket, func.sum(ledger.units))
                    .where(ledger.pt_day == pt_day, ledger.client_id.in_(client_ids))
                    .group_by(ledger.client_id, ledger.bucket)
                )
            ).all()
            by_client: dict[UUID, dict[str, int]] = {}
            for client_id, bucket, units in per_client:
                by_client.setdefault(client_id, {})[bucket] = int(units or 0)
            llm, tavily = await self._provider_day(session, pt_day, client_ids)
        quota = self.settings.quota
        youtube = YouTubeQuota(
            pt_day=pt_day,
            units_used=int(totals.get("main") or 0),
            units_limit=quota.main_daily_units,
            search_calls_used=int(totals.get("search") or 0),
            search_calls_limit=quota.search_daily_calls,
            reset_at=datetime.combine(pt_day + timedelta(days=1), time(), self.tz),
            by_client=[
                ClientQuota(
                    client_id=ids.encode(ids.CLIENT, client_id),
                    units=buckets.get("main", 0),
                    search_calls=buckets.get("search", 0),
                )
                for client_id, buckets in sorted(by_client.items(), key=lambda item: str(item[0]))
            ],
        )
        return OpsQuotaOut(youtube=youtube, llm=llm, tavily=tavily)

    async def _provider_day(
        self, session: Any, day: date, client_ids: list[UUID]
    ) -> tuple[LLMUsage, dict[str, float]]:
        # llm and tavily spend is tenant-scoped, on the utc calendar day
        start = datetime.combine(day, time(), UTC)
        ledger = ProviderUsageLedger
        window = (
            ledger.occurred_at >= start,
            ledger.occurred_at < start + timedelta(days=1),
            ledger.client_id.in_(client_ids),
        )
        model = func.coalesce(ledger.model_resolved, ledger.model_requested, "unknown")
        rows = (
            await session.execute(
                select(
                    model,
                    func.sum(func.coalesce(ledger.input_tokens, 0)),
                    func.sum(func.coalesce(ledger.output_tokens, 0)),
                    func.sum(func.coalesce(ledger.cache_read_tokens, 0)),
                    func.sum(func.coalesce(ledger.cost_usd, 0)),
                )
                .where(ledger.provider_kind == "llm", *window)
                .group_by(model)
            )
        ).all()
        models = [
            ModelUsage(
                model=name,
                input_tokens=int(tin),
                output_tokens=int(tout),
                cache_read_tokens=int(tread),
                usd=float(usd),
            )
            for name, tin, tout, tread, usd in rows
        ]
        credits = (
            await session.execute(
                select(func.coalesce(func.sum(ledger.credits), 0)).where(
                    ledger.provider_kind == "tavily", *window
                )
            )
        ).scalar_one()
        usd_per_credit = self.settings.evaluation.ops.tavily_usd_per_credit
        llm = LLMUsage(
            usd_today=round(sum(m.usd for m in models), 6), by_model=sorted(models, key=lambda m: m.model)
        )
        return llm, {"credits_today": float(credits), "usd_today": round(float(credits) * usd_per_credit, 4)}

    async def report(self, scope: TenantScope, start: datetime | None, end: datetime | None) -> OwnerReport:
        end = end or self.clock.now()
        start = start or end - timedelta(days=DEFAULT_REPORT_DAYS)
        builder = OpsReportBuilder(self.session_factory, self.settings, self.clock)
        report = await builder.build(start, end, client_ids=await self._client_ids(scope))
        payload = report.model_dump(mode="json")
        payload["scope"] = {
            "clients": "workspace",
            "project_quota": "project-wide (one Google Cloud project serves every workspace)",
            "retention": "project-wide",
        }
        return payload


@api_service_registry.register(OPS)
def build_ops_service(container: Any) -> LedgerOpsService:
    return LedgerOpsService(container.session_factory, container.settings, container.clock)
