from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.core.clock import Clock
from app.core.config import Settings
from app.core.db import SessionFactory
from app.evaluation.ops.models import OpsReport, StageTiming
from app.evaluation.ops.report import OpsReportBuilder
from app.models.evaluation import OpsDailyRollup
from app.models.tenancy import Client


def _totals(timings: dict[str, list[StageTiming]]) -> dict[str, dict[str, Any]]:
    return {
        kind: next(t.model_dump() for t in items if t.stage == "total")
        for kind, items in timings.items()
        if any(t.stage == "total" for t in items)
    }


def client_metrics(report: OpsReport, client_id: UUID) -> dict[str, Any]:
    quota = next((item for item in report.quota if item.client_id == client_id), None)
    llm = next((item for item in report.llm if item.client_id == client_id), None)
    tavily = next((item for item in report.tavily if item.client_id == client_id), None)
    return {
        "quota": None if quota is None else quota.model_dump(mode="json"),
        "llm_usd": None if llm is None else llm.usd,
        "llm_usd_per_daily_refresh": None if llm is None else llm.usd_per_daily_refresh,
        "tavily_credits": None if tavily is None else tavily.credits,
    }


def project_metrics(report: OpsReport) -> dict[str, Any]:
    llm = next((item for item in report.llm if item.client_id is None), None)
    tavily = next((item for item in report.tavily if item.client_id is None), None)
    return {
        "quota": [day.model_dump(mode="json") for day in report.project_quota],
        "run_totals": _totals(report.run_time),
        "llm_usd": None if llm is None else llm.usd,
        "tavily_credits": None if tavily is None else tavily.credits,
        "crawl": None if report.crawl is None else report.crawl.model_dump(mode="json"),
        "retention_compliant": report.retention.compliant,
        "capacity_estimate": report.capacity_estimate,
        "targets": report.targets,
    }


class OpsRollupTask:
    """materialises one day per client plus a project row (client_id null) so ops queries stay cheap."""

    def __init__(self, session_factory: SessionFactory, settings: Settings, clock: Clock) -> None:
        self.session_factory = session_factory
        self.builder = OpsReportBuilder(session_factory, settings, clock)
        self.clock = clock

    def yesterday(self) -> date:
        return (self.clock.now().astimezone(UTC) - timedelta(days=1)).date()

    async def roll(self, day: date) -> dict[str, Any]:
        # quota rows use the pacific-time day of the same date; everything else the utc day
        start = datetime.combine(day, time(), UTC)
        report = await self.builder.build(start, start + timedelta(days=1), quota_days=(day, day))
        seen = {item.client_id for item in report.quota}
        seen |= {item.client_id for item in [*report.llm, *report.tavily] if item.client_id is not None}
        client_ids = sorted(seen, key=str)
        async with self.session_factory() as session, session.begin():
            lookup = select(Client.id, Client.workspace_id).where(Client.id.in_(client_ids))
            workspaces = dict((await session.execute(lookup)).all()) if client_ids else {}
            rows = [
                {"day": day, "client_id": None, "workspace_id": None, "metrics": project_metrics(report)}
            ] + [
                {
                    "day": day,
                    "client_id": client_id,
                    "workspace_id": workspaces.get(client_id),
                    "metrics": client_metrics(report, client_id),
                }
                for client_id in client_ids
            ]
            statement = insert(OpsDailyRollup).values(rows)
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=["day", "client_id"],
                    set_={"metrics": statement.excluded.metrics, "updated_at": func.now()},
                )
            )
        return {"day": day.isoformat(), "rows": len(rows)}
