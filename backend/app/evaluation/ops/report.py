from collections.abc import Collection
from datetime import date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import app.retention.rules  # noqa: F401 - registers the retention rules the audit reads
from app.core.clock import Clock
from app.core.config import Settings
from app.core.db import SessionFactory
from app.evaluation.ops import aggregate
from app.evaluation.ops.models import (
    OpsReport,
    QuotaUsageSummary,
    RetentionStatus,
    StageTiming,
)
from app.evaluation.ops.sources import OpsSources
from app.retention.rules import retention_registry
from app.retention.sweeper import RetentionSweeper

ONBOARDING_TARGET_S = 8 * 60
NO_RUNS_NOTE = "No daily_refresh runs were recorded in this period; the planning figures below are estimates."


def pt_days(start: datetime, end: datetime, tz: ZoneInfo) -> tuple[date, date]:
    return start.astimezone(tz).date(), (end - timedelta(microseconds=1)).astimezone(tz).date()


class OpsReportBuilder:
    """quota (pacific-time days), runs, llm, tavily, crawl and retention for one period, read-only."""

    def __init__(
        self,
        session_factory: SessionFactory,
        settings: Settings,
        clock: Clock,
        sources: OpsSources | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.clock = clock
        self.sources = sources or OpsSources.default()
        self.quota_tz = ZoneInfo(settings.quota.timezone)

    async def build(
        self,
        start: datetime,
        end: datetime,
        client_ids: Collection[UUID] | None = None,
        quota_days: tuple[date, date] | None = None,
    ) -> OpsReport:
        if end <= start:
            raise ValueError("the report period must end after it starts")
        days = quota_days or pt_days(start, end, self.quota_tz)
        async with self.session_factory() as session:
            quota_rows = await self.sources.quota.rows(session, *days)
            hits = await self.sources.quota.cache_hits(session, *days)
            runs = await self.sources.runs.runs(session, start, end)
            stages = await self.sources.runs.stages(session, start, end)
            providers = await self.sources.providers.rows(session, start, end, ("llm", "tavily", "crawl"))
            crawl_p95 = await self.sources.providers.latency_p95(session, start, end, "crawl")
        wanted = set(client_ids) if client_ids is not None else None
        scoped_runs = [run for run in runs if wanted is None or run.client_id in wanted]
        scoped_stages = [row for row in stages if wanted is None or row.client_id in wanted]
        scoped_providers = [row for row in providers if wanted is None or row.client_id in wanted]
        quota, project_quota = aggregate.quota_summaries(
            quota_rows, hits, aggregate.refresh_runs_by_day(runs, self.quota_tz)
        )
        quota = [item for item in quota if wanted is None or item.client_id in wanted]
        refresh_counts = aggregate.refresh_run_counts(scoped_runs)
        run_time = aggregate.run_timings(scoped_runs, scoped_stages)
        llm_rows = [row for row in scoped_providers if row.provider_kind == "llm"]
        tavily_rows = [row for row in scoped_providers if row.provider_kind == "tavily"]
        crawl_rows = [row for row in scoped_providers if row.provider_kind == "crawl"]
        retention = await self._retention()
        clients = {run.client_id for run in scoped_runs} | {item.client_id for item in quota}
        clients |= {row.client_id for row in scoped_providers if row.client_id is not None}
        measured = any(item.daily_refresh_runs for item in quota) or bool(
            run_time.get(aggregate.DAILY_REFRESH)
        )
        notes = [] if measured else [NO_RUNS_NOTE]
        ops = self.settings.evaluation.ops
        if self._prices_stale():
            notes.append(
                f"LLM prices were last verified {ops.prices_verified_at.isoformat()}; re-check them."
            )
        return OpsReport(
            period_from=start,
            period_to=end,
            quota_days_pt=days,
            quota_timezone=self.settings.quota.timezone,
            generated_at=self.clock.now(),
            clients=len(clients),
            quota=quota,
            project_quota=project_quota,
            run_time=run_time,
            queue_wait=aggregate.queue_waits(scoped_runs),
            llm=aggregate.llm_summaries(llm_rows, refresh_counts),
            tavily=aggregate.tavily_usage(tavily_rows, ops.tavily_usd_per_credit),
            crawl=aggregate.crawl_usage(crawl_rows, crawl_p95 if wanted is None else None),
            provider_errors=aggregate.provider_errors(scoped_providers, project_quota),
            retention=retention,
            capacity_estimate=self._capacity(quota),
            targets=self._targets(run_time, quota, retention),
            prices_verified_at=ops.prices_verified_at,
            measured=measured,
            notes=notes,
        )

    async def _retention(self) -> RetentionStatus:
        rules = list(retention_registry.all().values())
        sweeper = RetentionSweeper(
            self.session_factory, self.clock, self.settings.policy, self.settings.retention
        )
        oldest, violations = await sweeper.audit(rules)
        checked = sum(1 for rule in rules if rule.hard_limit(self.settings.policy) is not None)
        return RetentionStatus(
            compliant=not violations,
            violations=violations,
            oldest_age_days={
                name: round(age.total_seconds() / 86400, 2) for name, age in sorted(oldest.items())
            },
            rules_checked=checked,
        )

    def _limits(self) -> dict[str, int]:
        quota = self.settings.quota
        return {
            "main": quota.main_daily_units,
            "search": quota.search_daily_calls,
            "batch_stats": quota.batch_stats_daily_units,
        }

    @staticmethod
    def client_day_units(quota: list[QuotaUsageSummary], bucket: str) -> list[float]:
        # client-days that ran a daily refresh; amortised so shared cache fills count once across clients
        return [item.buckets[bucket].units_amortised for item in quota if item.daily_refresh_runs]

    def _capacity(self, quota: list[QuotaUsageSummary]) -> dict[str, float | None]:
        capacity: dict[str, float | None] = {}
        for bucket, limit in self._limits().items():
            typical = aggregate.median_or_none(self.client_day_units(quota, bucket))
            capacity[bucket] = round(limit / typical, 1) if typical else None
        return capacity

    def _prices_stale(self) -> bool:
        ops = self.settings.evaluation.ops
        return (self.clock.now().date() - ops.prices_verified_at).days > ops.price_stale_days

    def _targets(
        self,
        run_time: dict[str, list[StageTiming]],
        quota: list[QuotaUsageSummary],
        retention: RetentionStatus,
    ) -> dict[str, bool | None]:
        target_ms = self.settings.evaluation.ops.latency_target_s * 1000
        refresh = _total(run_time.get(aggregate.DAILY_REFRESH))
        onboarding = _total(run_time.get("onboarding"))
        typical_main = aggregate.median_or_none(self.client_day_units(quota, "main"))
        return {
            "daily_refresh_p95_under_120s": None if refresh is None else refresh < target_ms,
            "onboarding_p95_under_8min": None
            if onboarding is None
            else onboarding < ONBOARDING_TARGET_S * 1000,
            "units_per_client_within_budget": None
            if typical_main is None
            else typical_main <= self.settings.evaluation.ops.units_per_client_budget,
            "llm_prices_fresh": not self._prices_stale(),
            "retention_compliant": retention.compliant,
        }


def _total(timings: list[StageTiming] | None) -> int | None:
    if not timings:
        return None
    return next((t.p95_ms for t in timings if t.stage == aggregate.TOTAL), None)
