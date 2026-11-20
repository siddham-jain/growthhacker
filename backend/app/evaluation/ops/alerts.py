"""alert rules over the ledgers; the evaluator fires, dedupes and auto-resolves alert_event rows."""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from statistics import median
from typing import Any, Literal, Protocol
from zoneinfo import ZoneInfo

import httpx
import numpy as np
from sqlalchemy import func, select

import app.retention.rules  # noqa: F401 - registers the retention rules the audit reads
from app.core.clock import Clock, FrozenClock
from app.core.config import Settings
from app.core.db import SessionFactory
from app.core.logging import get_logger
from app.core.registry import Registry
from app.models.evaluation import AlertEvent, EvalFreeze
from app.models.ledgers import ProviderUsageLedger, YtQuotaLedger
from app.models.runs import PipelineRun
from app.retention.rules import retention_registry
from app.retention.sweeper import RetentionSweeper

logger = get_logger(__name__)

Severity = Literal["warn", "critical"]
DAY = timedelta(hours=24)
WEEK = timedelta(days=7)
QUOTA_WARN_BEFORE_HOUR_PT = 18
COMPLETED = ("succeeded", "partial")
TERMINAL = ("succeeded", "partial", "failed", "cancelled", "superseded")


@dataclass(frozen=True)
class Finding:
    key: str
    severity: Severity | None
    value: float
    threshold: float
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AlertContext:
    session_factory: SessionFactory
    settings: Settings
    now: datetime


class AlertRule(Protocol):
    rule_id: str

    async def evaluate(self, ctx: AlertContext) -> list[Finding]: ...


alert_rule_registry: Registry[type[AlertRule]] = Registry("alert_rule_registry")


def graded(value: float, warn: float | None, critical: float | None) -> Severity | None:
    if critical is not None and value > critical:
        return "critical"
    if warn is not None and value > warn:
        return "warn"
    return None


def finding(rule_id: str, subject: str | None, value: float, warn: float | None, critical: float) -> Finding:
    severity = graded(value, warn, critical)
    threshold = critical if severity == "critical" or warn is None else warn
    key = rule_id if subject is None else f"{rule_id}:{subject}"
    return Finding(key, severity, round(value, 6), threshold, {"subject": subject} if subject else {})


@alert_rule_registry.register("project_quota_used")
class ProjectQuotaUsedRule:
    """share of today's pacific-time quota used per bucket; warn only matters before 18:00 pt."""

    rule_id = "project_quota_used"
    buckets = ("main", "batch_stats")

    async def evaluate(self, ctx: AlertContext) -> list[Finding]:
        quota = ctx.settings.quota
        local = ctx.now.astimezone(ZoneInfo(quota.timezone))
        limits = {"main": quota.main_daily_units, "batch_stats": quota.batch_stats_daily_units}
        used = await _units_today(ctx, local.date())
        warn = 0.70 if local.hour < QUOTA_WARN_BEFORE_HOUR_PT else None
        return [finding(self.rule_id, b, used.get(b, 0) / limits[b], warn, 0.90) for b in self.buckets]


@alert_rule_registry.register("search_calls_today")
class SearchCallsTodayRule:
    rule_id = "search_calls_today"

    async def evaluate(self, ctx: AlertContext) -> list[Finding]:
        quota = ctx.settings.quota
        local = ctx.now.astimezone(ZoneInfo(quota.timezone))
        calls = (await _units_today(ctx, local.date())).get("search", 0)
        limit = quota.search_daily_calls
        return [finding(self.rule_id, None, calls, 0.70 * limit, 0.90 * limit)]


async def _units_today(ctx: AlertContext, day: Any) -> dict[str, int]:
    statement = (
        select(YtQuotaLedger.bucket, func.sum(YtQuotaLedger.units))
        .where(YtQuotaLedger.pt_day == day)
        .group_by(YtQuotaLedger.bucket)
    )
    async with ctx.session_factory() as session:
        return {bucket: int(units or 0) for bucket, units in await session.execute(statement)}


@alert_rule_registry.register("units_per_client_refresh")
class UnitsPerClientRefreshRule:
    """7-day median of main-bucket units per daily_refresh run, per client."""

    rule_id = "units_per_client_refresh"

    async def evaluate(self, ctx: AlertContext) -> list[Finding]:
        budget = ctx.settings.evaluation.ops.units_per_client_budget
        statement = (
            select(PipelineRun.client_id, PipelineRun.id, func.coalesce(func.sum(YtQuotaLedger.units), 0))
            .outerjoin(
                YtQuotaLedger, (YtQuotaLedger.run_id == PipelineRun.id) & (YtQuotaLedger.bucket == "main")
            )
            .where(PipelineRun.kind == "daily_refresh", PipelineRun.started_at >= ctx.now - WEEK)
            .group_by(PipelineRun.client_id, PipelineRun.id)
        )
        per_client: dict[Any, list[int]] = defaultdict(list)
        async with ctx.session_factory() as session:
            for client_id, _, units in await session.execute(statement):
                per_client[client_id].append(int(units))
        return [
            finding(self.rule_id, str(client), float(median(units)), budget, 2 * budget)
            for client, units in sorted(per_client.items(), key=lambda item: str(item[0]))
        ]


@alert_rule_registry.register("daily_refresh_p95")
class DailyRefreshP95Rule:
    rule_id = "daily_refresh_p95"

    async def evaluate(self, ctx: AlertContext) -> list[Finding]:
        target = ctx.settings.evaluation.ops.latency_target_s
        statement = select(PipelineRun.started_at, PipelineRun.finished_at).where(
            PipelineRun.kind == "daily_refresh",
            PipelineRun.status.in_(COMPLETED),
            PipelineRun.finished_at >= ctx.now - DAY,
            PipelineRun.started_at.is_not(None),
        )
        async with ctx.session_factory() as session:
            walls = [(end - start).total_seconds() for start, end in await session.execute(statement)]
        if not walls:
            return []
        p95 = float(np.quantile(np.asarray(walls), 0.95))
        return [finding(self.rule_id, None, p95, target, 2 * target)]


@alert_rule_registry.register("run_failure_rate")
class RunFailureRateRule:
    rule_id = "run_failure_rate"

    async def evaluate(self, ctx: AlertContext) -> list[Finding]:
        statement = select(PipelineRun.status).where(
            PipelineRun.finished_at >= ctx.now - DAY, PipelineRun.status.in_(TERMINAL)
        )
        async with ctx.session_factory() as session:
            statuses = list((await session.execute(statement)).scalars())
        if not statuses:
            return []
        rate = sum(status == "failed" for status in statuses) / len(statuses)
        return [finding(self.rule_id, None, rate, 0.05, 0.15)]


@alert_rule_registry.register("llm_cost_per_client_day")
class LlmCostPerClientDayRule:
    rule_id = "llm_cost_per_client_day"

    async def evaluate(self, ctx: AlertContext) -> list[Finding]:
        ledger = ProviderUsageLedger
        statement = (
            select(ledger.client_id, func.sum(func.coalesce(ledger.cost_usd, 0)))
            .where(
                ledger.provider_kind == "llm",
                ledger.occurred_at >= ctx.now - DAY,
                ledger.client_id.is_not(None),
            )
            .group_by(ledger.client_id)
        )
        async with ctx.session_factory() as session:
            rows = sorted((await session.execute(statement)).all(), key=lambda row: str(row[0]))
        return [finding(self.rule_id, str(client), float(usd), 0.75, 2.0) for client, usd in rows]


@alert_rule_registry.register("crawl_error_rate")
class CrawlErrorRateRule:
    rule_id = "crawl_error_rate"

    async def evaluate(self, ctx: AlertContext) -> list[Finding]:
        ledger = ProviderUsageLedger
        statement = (
            select(ledger.outcome, func.count())
            .where(ledger.provider_kind == "crawl", ledger.occurred_at >= ctx.now - DAY)
            .group_by(ledger.outcome)
        )
        async with ctx.session_factory() as session:
            counts = dict((await session.execute(statement)).all())
        total = sum(counts.values())
        if not total:
            return []
        return [finding(self.rule_id, None, (total - counts.get("ok", 0)) / total, 0.20, 0.50)]


@alert_rule_registry.register("retention_violations")
class RetentionViolationsRule:
    """any api-data row older than its hard limit (01's audit) is critical."""

    rule_id = "retention_violations"

    async def evaluate(self, ctx: AlertContext) -> list[Finding]:
        settings = ctx.settings
        sweeper = RetentionSweeper(
            ctx.session_factory, FrozenClock(ctx.now), settings.policy, settings.retention
        )
        _, violations = await sweeper.audit(list(retention_registry.all().values()))
        result = finding(self.rule_id, None, len(violations), None, 0)
        return [replace(result, payload={"violations": violations[:20]})]


@alert_rule_registry.register("freeze_bundle_expired")
class FreezeBundleExpiredRule:
    rule_id = "freeze_bundle_expired"

    async def evaluate(self, ctx: AlertContext) -> list[Finding]:
        statement = select(func.count()).where(
            EvalFreeze.expires_at < ctx.now, EvalFreeze.purged_at.is_(None)
        )
        async with ctx.session_factory() as session:
            expired = int((await session.execute(statement)).scalar_one())
        return [finding(self.rule_id, None, expired, None, 0)]


@dataclass(frozen=True)
class AlertNotice:
    action: Literal["fired", "resolved"]
    rule_id: str
    severity: str
    value: float
    threshold: float
    at: datetime
    payload: dict[str, Any]


class AlertNotifier(Protocol):
    async def notify(self, notice: AlertNotice) -> None: ...


class LogNotifier:
    async def notify(self, notice: AlertNotice) -> None:
        log = logger.error if notice.action == "fired" else logger.info
        log(
            f"ops.alert.{notice.action}",
            rule_id=notice.rule_id,
            severity=notice.severity,
            value=notice.value,
            threshold=notice.threshold,
        )


class WebhookNotifier:
    """slack-compatible incoming webhook; a failed post is logged and never blocks alert state."""

    def __init__(self, http: httpx.AsyncClient, url: str) -> None:
        self._http = http
        self._url = url

    async def notify(self, notice: AlertNotice) -> None:
        verb = "FIRED" if notice.action == "fired" else "resolved"
        text = (
            f"[{notice.severity}] {notice.rule_id} {verb}: {notice.value:g} (threshold {notice.threshold:g})"
        )
        try:
            response = await self._http.post(self._url, json={"text": text}, timeout=10.0)
            response.raise_for_status()
        except httpx.HTTPError as error:
            logger.error("ops.alert.webhook_failed", rule_id=notice.rule_id, error=type(error).__name__)


@dataclass
class EvaluationSummary:
    findings: list[Finding]
    fired: list[str] = field(default_factory=list)
    deduped: list[str] = field(default_factory=list)
    resolved: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        active = [f.key for f in self.findings if f.severity]
        return {"active": active, "fired": self.fired, "deduped": self.deduped, "resolved": self.resolved}


class AlertEvaluator:
    def __init__(
        self,
        session_factory: SessionFactory,
        settings: Settings,
        clock: Clock,
        notifiers: Sequence[AlertNotifier],
        rules: Sequence[AlertRule] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.clock = clock
        self.notifiers = list(notifiers)
        self.rules = (
            list(rules) if rules is not None else [rule() for rule in alert_rule_registry.all().values()]
        )

    async def findings(self) -> list[Finding]:
        ctx = AlertContext(self.session_factory, self.settings, self.clock.now())
        return [item for rule in self.rules for item in await rule.evaluate(ctx)]

    async def evaluate(self, *, dry_run: bool = False) -> EvaluationSummary:
        summary = EvaluationSummary(await self.findings())
        if dry_run:
            return summary
        now = self.clock.now()
        dedupe = timedelta(minutes=self.settings.evaluation.ops.alert_dedupe_minutes)
        rule_ids = {rule.rule_id for rule in self.rules}
        notices: list[AlertNotice] = []
        async with self.session_factory() as session, session.begin():
            open_events = {
                event.rule_id: event
                for event in (
                    await session.execute(select(AlertEvent).where(AlertEvent.resolved_at.is_(None)))
                ).scalars()
            }
            recent = set(
                (
                    await session.execute(
                        select(AlertEvent.rule_id).where(AlertEvent.resolved_at >= now - dedupe)
                    )
                ).scalars()
            )
            active = {item.key: item for item in summary.findings if item.severity}
            for key, item in sorted(active.items()):
                current = open_events.get(key)
                if current is not None and current.severity == item.severity:
                    summary.deduped.append(key)
                    continue
                if current is not None:
                    current.resolved_at = now
                elif key in recent:
                    summary.deduped.append(key)
                    continue
                session.add(
                    AlertEvent(
                        rule_id=key,
                        severity=item.severity,
                        value=item.value,
                        threshold=item.threshold,
                        fired_at=now,
                        payload=item.payload,
                    )
                )
                summary.fired.append(key)
                notices.append(
                    AlertNotice("fired", key, item.severity, item.value, item.threshold, now, item.payload)
                )
            for key, event in sorted(open_events.items()):
                if key in active or key.split(":", 1)[0] not in rule_ids:
                    continue
                event.resolved_at = now
                summary.resolved.append(key)
                notices.append(
                    AlertNotice(
                        "resolved", key, event.severity, event.value, event.threshold, now, event.payload
                    )
                )
        for notice in notices:
            for notifier in self.notifiers:
                await notifier.notify(notice)
        return summary


def default_notifiers(settings: Settings, http: httpx.AsyncClient | None) -> list[AlertNotifier]:
    notifiers: list[AlertNotifier] = [LogNotifier()]
    url = settings.evaluation.ops.alert_webhook_url
    if url is not None and http is not None:
        notifiers.append(WebhookNotifier(http, url.get_secret_value()))
    return notifiers
