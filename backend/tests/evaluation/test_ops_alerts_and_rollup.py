from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.core.clock import FrozenClock
from app.core.registry import Registry
from app.evaluation.ops import hooks
from app.evaluation.ops.alerts import (
    AlertContext,
    AlertEvaluator,
    AlertNotice,
    FreezeBundleExpiredRule,
    ProjectQuotaUsedRule,
    RunFailureRateRule,
    UnitsPerClientRefreshRule,
    alert_rule_registry,
)
from app.evaluation.ops.rollup import OpsRollupTask
from app.models.evaluation import AlertEvent, OpsDailyRollup
from tests.conftest import make_settings
from tests.evaluation import ops_fixtures as fx

pytestmark = pytest.mark.db


class RecordingNotifier:
    def __init__(self) -> None:
        self.notices: list[AlertNotice] = []

    async def notify(self, notice: AlertNotice) -> None:
        self.notices.append(notice)


async def test_alert_fires_dedupes_and_resolves(session_factory) -> None:
    scope = await fx.tenant(session_factory, "alerts")
    for _ in range(3):
        await fx.run(session_factory, scope, status="failed", started_at=fx.NOW - timedelta(hours=3))
    await fx.run(session_factory, scope, started_at=fx.NOW - timedelta(hours=3))
    clock = FrozenClock(fx.NOW)
    notifier = RecordingNotifier()
    evaluator = AlertEvaluator(
        session_factory, make_settings(), clock, [notifier], rules=[RunFailureRateRule()]
    )

    first = await evaluator.evaluate()
    assert first.fired == ["run_failure_rate"]
    assert [n.severity for n in notifier.notices] == ["critical"]

    second = await evaluator.evaluate()
    assert (second.fired, second.deduped) == ([], ["run_failure_rate"])

    clock.advance(timedelta(days=2))
    third = await evaluator.evaluate()
    assert third.resolved == ["run_failure_rate"]
    async with session_factory() as session:
        events = (await session.execute(select(AlertEvent))).scalars().all()
    assert len(events) == 1 and events[0].resolved_at == clock.now()
    assert [n.action for n in notifier.notices] == ["fired", "resolved"]


async def test_dry_run_writes_nothing(session_factory) -> None:
    scope = await fx.tenant(session_factory, "dry")
    await fx.run(session_factory, scope, status="failed", started_at=fx.NOW - timedelta(hours=3))
    evaluator = AlertEvaluator(
        session_factory, make_settings(), FrozenClock(fx.NOW), [], rules=[RunFailureRateRule()]
    )
    summary = await evaluator.evaluate(dry_run=True)
    assert [f.severity for f in summary.findings] == ["critical"]
    async with session_factory() as session:
        assert (await session.execute(select(func.count()).select_from(AlertEvent))).scalar_one() == 0


@pytest.mark.parametrize(
    ("units", "hour_utc", "expected"),
    [(7_500, 18, "warn"), (7_500, 3, None), (9_500, 3, "critical"), (100, 18, None)],
)
async def test_project_quota_warns_only_before_six_pm_pacific(
    session_factory, units, hour_utc, expected
) -> None:
    # 18:00 utc is 10:00 pt; 03:00 utc next day is 19:00 pt on the same pt day
    at = datetime(2026, 11, 21, hour_utc, tzinfo=UTC) + (timedelta(days=1) if hour_utc < 8 else timedelta())
    await fx.quota(session_factory, units)
    ctx = AlertContext(session_factory, make_settings(), at)
    main = next(f for f in await ProjectQuotaUsedRule().evaluate(ctx) if f.key == "project_quota_used:main")
    assert main.severity == expected


async def test_units_per_client_refresh_uses_the_median(session_factory) -> None:
    scope = await fx.tenant(session_factory, "units")
    for units in (100, 200, 400):
        run_id = await fx.run(session_factory, scope)
        await fx.quota(session_factory, units, client_id=scope.client_id, run_id=run_id)
    [result] = await UnitsPerClientRefreshRule().evaluate(
        AlertContext(session_factory, make_settings(), fx.NOW)
    )
    assert (result.value, result.severity) == (200, "warn")


async def test_expired_freeze_rule_without_rows_is_quiet(session_factory) -> None:
    [result] = await FreezeBundleExpiredRule().evaluate(
        AlertContext(session_factory, make_settings(), fx.NOW)
    )
    assert result.severity is None


def test_every_rule_is_registered() -> None:
    assert {
        "project_quota_used",
        "search_calls_today",
        "units_per_client_refresh",
        "daily_refresh_p95",
        "run_failure_rate",
        "llm_cost_per_client_day",
        "crawl_error_rate",
        "retention_violations",
        "freeze_bundle_expired",
    } <= set(alert_rule_registry.keys())


async def test_rollup_upsert_is_idempotent(session_factory) -> None:
    scope = await fx.tenant(session_factory, "rollup")
    run_id = await fx.run(session_factory, scope, started_at=datetime(2026, 11, 21, 12, tzinfo=UTC))
    await fx.quota(session_factory, 30, client_id=scope.client_id, run_id=run_id)
    task = OpsRollupTask(session_factory, make_settings(), FrozenClock(datetime(2026, 11, 22, 1, tzinfo=UTC)))
    assert task.yesterday() == fx.PT_DAY

    first = await task.roll(fx.PT_DAY)
    await fx.quota(session_factory, 5, client_id=scope.client_id, run_id=run_id)
    second = await task.roll(fx.PT_DAY)

    assert first["rows"] == second["rows"] == 2
    async with session_factory() as session:
        rows = (
            (await session.execute(select(OpsDailyRollup).where(OpsDailyRollup.day == fx.PT_DAY)))
            .scalars()
            .all()
        )
    assert len(rows) == 2
    client_row = next(row for row in rows if row.client_id == scope.client_id)
    assert client_row.workspace_id == scope.workspace_id
    assert client_row.metrics["quota"]["buckets"]["main"]["units_client"] == 35
    project_row = next(row for row in rows if row.client_id is None)
    assert project_row.metrics["run_totals"]["daily_refresh"]["n"] == 1


async def test_nightly_hooks_run_by_name() -> None:
    registry = Registry("test_hooks")

    async def purge(container):
        return {"purged": 2, "container": container}

    registry.add("purge", purge)
    assert await hooks.run_nightly_hooks("c", registry) == {"purge": {"purged": 2, "container": "c"}}


def test_maintenance_tasks_are_registered() -> None:
    import app.evaluation.ops.tasks  # noqa: F401
    from app.workers.tasks import OPS_ALERTS, OPS_ROLLUP, maintenance_task_registry

    assert OPS_ROLLUP in maintenance_task_registry and OPS_ALERTS in maintenance_task_registry
