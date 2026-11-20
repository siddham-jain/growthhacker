import uuid
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.api import ids
from app.core.clock import FrozenClock
from app.core.tenancy import TenantScope
from app.evaluation.ops.aggregate import run_timings
from app.evaluation.ops.api import LedgerOpsService
from app.evaluation.ops.render import render_ops_report
from app.evaluation.ops.report import OpsReportBuilder, pt_days
from app.evaluation.ops.sources import RunRow
from tests.conftest import make_settings
from tests.evaluation import ops_fixtures as fx

pytestmark = pytest.mark.db

START = fx.NOW - timedelta(days=1)


def builder(session_factory) -> OpsReportBuilder:
    return OpsReportBuilder(session_factory, make_settings(), FrozenClock(fx.NOW))


def test_quota_days_follow_pacific_time() -> None:
    tz = ZoneInfo("America/Los_Angeles")
    start = datetime(2026, 11, 20, 0, tzinfo=UTC)
    assert pt_days(start, start + timedelta(days=1), tz) == (date(2026, 11, 19), date(2026, 11, 20))


async def test_amortised_attribution_sums_to_project_total(session_factory) -> None:
    a = await fx.tenant(session_factory, "a")
    b = await fx.tenant(session_factory, "b")
    run_a = await fx.run(session_factory, a)
    run_b = await fx.run(session_factory, b)
    await fx.quota(session_factory, 30, client_id=a.client_id, run_id=run_a)
    await fx.quota(session_factory, 10, client_id=a.client_id, endpoint="channels.list")
    await fx.quota(session_factory, 20, client_id=b.client_id, run_id=run_b)
    await fx.quota(
        session_factory, 3, bucket="search", client_id=b.client_id, run_id=run_b, endpoint="search.list"
    )
    await fx.quota(session_factory, 40)
    await fx.quota(session_factory, 12, bucket="batch_stats")
    await fx.quota(session_factory, 99, day=fx.PT_DAY - timedelta(days=3))
    await fx.cache_hits(session_factory, a.client_id, 3)
    await fx.cache_hits(session_factory, b.client_id, 1)

    report = await builder(session_factory).build(START, fx.NOW + timedelta(hours=1))

    by_client = {item.client_id: item for item in report.quota}
    [project] = report.project_quota
    for bucket in ("main", "search", "batch_stats"):
        attributed = sum(item.buckets[bucket].units_amortised for item in report.quota)
        assert attributed + project.unattributed_shared[bucket] == pytest.approx(project.units[bucket])
    summary_a = by_client[a.client_id]
    assert summary_a.buckets["main"].units_amortised == pytest.approx(40 + 30)
    assert summary_a.units_per_daily_refresh["main"] == 30
    assert summary_a.amortised_per_daily_refresh["main"] == pytest.approx(60)
    assert by_client[b.client_id].units_per_daily_refresh["search"] == 3
    assert summary_a.cache_hit_rate == pytest.approx(3 / (3 + 2))
    assert report.capacity_estimate["main"] == pytest.approx(10_000 / 50)
    assert report.measured


async def test_shared_units_stay_unattributed_without_cache_hits(session_factory) -> None:
    await fx.quota(session_factory, 25)
    report = await builder(session_factory).build(START, fx.NOW + timedelta(hours=1))
    [project] = report.project_quota
    assert project.unattributed_shared["main"] == 25
    assert report.quota == []
    assert not report.measured
    assert report.targets["daily_refresh_p95_under_120s"] is None


async def test_stage_timings_p95_and_latency_target(session_factory) -> None:
    scope = await fx.tenant(session_factory, "t")
    for wall in [40.0] * 19 + [400.0]:
        await fx.run(session_factory, scope, wall_s=wall)
    slow = await fx.run(session_factory, scope, status="failed", wall_s=10)
    await fx.stage(session_factory, scope, slow, "ingest", 9000, status="failed")
    report = await builder(session_factory).build(START, fx.NOW + timedelta(hours=1))

    timings = {t.stage: t for t in report.run_time["daily_refresh"]}
    total = timings["total"]
    assert total.n == 20
    assert total.p95_ms == pytest.approx(40_000 + 0.05 * 360_000, abs=1)
    assert total.failure_rate == pytest.approx(1 / 21)
    assert timings["ingest"].failure_rate == 1.0
    assert report.targets["daily_refresh_p95_under_120s"] is True
    assert report.queue_wait["daily_refresh"].p50_ms == 500


def test_run_timings_ignore_unfinished_runs() -> None:
    started = fx.NOW
    rows = [
        RunRow(
            uuid.uuid4(),
            uuid.uuid4(),
            uuid.uuid4(),
            "daily_refresh",
            "succeeded",
            started,
            started + timedelta(seconds=300),
            None,
        ),
        RunRow(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), "daily_refresh", "running", started, None, None),
    ]
    [total] = run_timings(rows, [])["daily_refresh"]
    assert (total.n, total.p95_ms, total.failure_rate) == (1, 300_000, 0.0)


async def test_llm_and_tavily_costs_per_client(session_factory) -> None:
    scope = await fx.tenant(session_factory, "llm")
    run_id = await fx.run(session_factory, scope)
    await fx.provider(
        session_factory, "llm", client_id=scope.client_id, run_id=run_id, usd=0.12, model="claude-sonnet-5-5"
    )
    await fx.provider(
        session_factory,
        "llm",
        client_id=scope.client_id,
        run_id=run_id,
        usd=0.04,
        attempt_no=2,
        outcome="error",
    )
    await fx.provider(session_factory, "tavily", client_id=scope.client_id, credits=5)
    await fx.provider(session_factory, "crawl", outcome="blocked")
    await fx.provider(session_factory, "crawl")
    report = await builder(session_factory).build(START, fx.NOW + timedelta(hours=1))

    client_llm = next(item for item in report.llm if item.client_id == scope.client_id)
    assert client_llm.usd == pytest.approx(0.16)
    assert client_llm.usd_per_daily_refresh == pytest.approx(0.16)
    assert (client_llm.retries, client_llm.errors) == (1, 1)
    tavily = next(item for item in report.tavily if item.client_id == scope.client_id)
    assert (tavily.credits, tavily.usd) == (5, pytest.approx(0.04))
    assert report.crawl is not None and report.crawl.error_rate == 0.5
    assert report.provider_errors["crawl"] == {"blocked": 1}


async def test_render_names_the_target_and_planning_figures(session_factory) -> None:
    scope = await fx.tenant(session_factory, "r")
    await fx.run(session_factory, scope, wall_s=35)
    report = await builder(session_factory).build(START, fx.NOW + timedelta(hours=1))
    markdown = render_ops_report(report)
    assert "## Time to generate recommendations" in markdown
    assert "| total | 1 | 35.0 | 35.0 | 35.0 | 0.0% |" in markdown
    assert "Planning figures (estimates, not measurements)" in markdown
    assert "| Daily refresh p95 wall time < 120 s (warm cache) | met |" in markdown

    empty = await builder(session_factory).build(START - timedelta(days=30), START - timedelta(days=29))
    empty_markdown = render_ops_report(empty)
    assert "No daily_refresh runs measured." in empty_markdown
    assert "planning figures below are estimates" in empty_markdown


async def test_ops_service_scopes_clients_to_the_workspace(session_factory) -> None:
    mine = await fx.tenant(session_factory, "mine")
    other = await fx.tenant(session_factory, "other")
    await fx.quota(session_factory, 7, client_id=mine.client_id)
    await fx.quota(session_factory, 5, client_id=other.client_id)
    await fx.quota(session_factory, 2, bucket="search", client_id=mine.client_id)
    await fx.provider(session_factory, "llm", client_id=mine.client_id, usd=0.5, model="claude-haiku-4-5")
    service = LedgerOpsService(session_factory, make_settings(), FrozenClock(fx.NOW))

    out = await service.quota(TenantScope(workspace_id=mine.workspace_id), None)

    assert out.youtube.pt_day == fx.PT_DAY
    assert out.youtube.units_used == 12
    assert [(c.client_id, c.units, c.search_calls) for c in out.youtube.by_client] == [
        (ids.encode(ids.CLIENT, mine.client_id), 7, 2)
    ]
    assert out.llm.usd_today == pytest.approx(0.5)
    payload = await service.report(
        TenantScope(workspace_id=mine.workspace_id), START, fx.NOW + timedelta(hours=1)
    )
    assert payload["scope"]["clients"] == "workspace"
    assert {item["client_id"] for item in payload["quota"]} == {str(mine.client_id)}
