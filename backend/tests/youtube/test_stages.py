import asyncio
import uuid
from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest

from app.core.registry import stage_registry
from app.domain.common import ChannelRole
from app.domain.runs import RunKind, RunNoticeCode, RunTrigger
from app.domain.youtube import QuotaBucket
from app.pipeline.context import RunBudgetLedger
from app.pipeline.contracts import (
    BudgetKind,
    RunBudget,
    RunConfigSnapshot,
    RunContext,
    StageBudget,
    UsageLedger,
)
from app.pipeline.stages.youtube import (
    IngestStage,
    OwnAnalyticsStage,
    PrefetchOwnChannelStage,
    youtube_context,
)
from app.ports.quota import QuotaExhausted
from app.services.youtube.ingest import ChannelIngestResult
from tests.conftest import make_settings
from tests.contracts.test_pipeline_contracts import EmptyArtifacts, RecordingReporter, Token
from tests.youtube.fixtures import CHANNEL_A, CHANNEL_B, NOW, channel_record, video_id, video_record
from tests.youtube.test_quota import manager

OTHER = "UCtestChannelC000000000C"


class Ledger:
    def __init__(self) -> None:
        self.charged: dict[BudgetKind, float] = {}

    def ensure(self, kind, amount) -> None: ...

    def charge(self, kind, amount) -> None:
        self.charged[kind] = self.charged.get(kind, 0) + amount

    def remaining(self, kind):
        return 500 if kind is BudgetKind.youtube_units else None

    def run_remaining(self, kind):
        return self.remaining(kind)

    def usage(self) -> UsageLedger:
        return UsageLedger()


class Reporter(RecordingReporter):
    def __init__(self) -> None:
        super().__init__()
        self.insights: list[Any] = []
        self.notices: list[Any] = []

    async def insight(self, message, payload) -> None:
        self.insights.append(payload)

    async def notice(self, notice) -> None:
        self.notices.append(notice)


class FakeQuota:
    def __init__(self) -> None:
        self.caps: dict = {}
        self.used = {bucket: 0 for bucket in QuotaBucket}

    async def open_run_budget(self, run_id, caps) -> None:
        self.caps = caps

    async def run_usage(self, run_id, stage=None):
        return dict(self.used)


class FakeIngest:
    def __init__(self, quota: FakeQuota) -> None:
        self.quota = quota
        self.calls: list[tuple[str, ChannelRole, str]] = []

    async def ingest_channel(self, channel_id, role, ctx, *, mode, languages):
        self.calls.append((channel_id, role, mode))
        self.quota.used[QuotaBucket.main] += 3
        if channel_id == OTHER:
            return ChannelIngestResult(channel_id=channel_id, status="not_found", message="gone")
        if channel_id == CHANNEL_B:
            return ChannelIngestResult(
                channel_id=channel_id,
                status="quota_limited",
                quota_bucket=QuotaBucket.main,
                quota_resets_at=NOW + timedelta(hours=3),
                channel=channel_record(channel_id),
            )
        return ChannelIngestResult(
            channel_id=channel_id,
            channel=channel_record(channel_id),
            videos=[video_record(video_id(1), channel_id=channel_id)],
            new_video_ids=[video_id(1)],
        )

    async def ensure_baseline(self, channel_ids, ctx):
        return [
            await self.ingest_channel(channel_ids[0], ChannelRole.creator, ctx, mode="baseline", languages=[])
        ]


class FakeLedgerSink:
    async def flush(self) -> int:
        return 0


class FakeLayer:
    def __init__(self) -> None:
        self.settings = make_settings()
        self.quota = FakeQuota()
        self.ingest = FakeIngest(self.quota)
        self.ledger = FakeLedgerSink()
        self.clock = type("Clock", (), {"now": staticmethod(lambda: NOW)})()


class Deps:
    def __init__(self, layer: FakeLayer) -> None:
        self.layer = layer

    def adapter(self, name: str):
        assert name == "youtube_data_layer"
        return self.layer


def context(
    layer: FakeLayer,
    *,
    kind: RunKind = RunKind.analysis,
    competitors=(CHANNEL_B,),
    own=CHANNEL_A,
    oauth: bool = False,
) -> tuple[RunContext, Reporter, Ledger]:
    snapshot = RunConfigSnapshot(
        workspace_id=uuid.uuid4(),
        client_id=uuid.uuid4(),
        config_version=1,
        config_hash="h",
        window_days=30,
        own_channel_id=own,
        competitor_channel_ids=list(competitors),
        topic_set_version=1,
        oauth_connected=oauth,
        market={"country": "IN", "language": "en-IN", "timezone": "Asia/Kolkata"},
    )
    reporter, ledger = Reporter(), Ledger()
    ctx = RunContext(
        run_id=uuid.uuid4(),
        kind=kind,
        trigger=RunTrigger.user,
        attempt=1,
        as_of=NOW,
        snapshot=snapshot,
        clock=layer.clock,
        budget=ledger,
        reporter=reporter,
        artifacts=EmptyArtifacts(),
        cancel=Token(),
        deps=Deps(layer),
    )
    return ctx, reporter, ledger


def test_stages_are_registered_under_plan_11_names() -> None:
    for name in ("ingest", "prefetch_own_channel", "own_analytics"):
        assert stage_registry.get(name).name == name


async def test_ingest_stage_degrades_on_quota_and_skips_the_rest() -> None:
    layer = FakeLayer()
    ctx, reporter, ledger = context(layer, competitors=(CHANNEL_B, OTHER))
    result = await IngestStage().run(ctx)
    assert result.status == "degraded"
    assert [call[0] for call in layer.ingest.calls] == [CHANNEL_A, CHANNEL_B]
    assert [item.status for item in result.artifact.channels] == ["ok", "quota_limited", "skipped"]
    notice = result.notices[0]
    assert notice.code is RunNoticeCode.quota_limited and "IST" in notice.message
    assert ledger.charged[BudgetKind.youtube_units] == 6
    assert layer.quota.caps[QuotaBucket.main] == 400
    assert len(reporter.insights) == 2 and reporter.insights[0].count == 1


async def test_daily_refresh_ingest_is_incremental_and_reports_missing_competitors() -> None:
    layer = FakeLayer()
    ctx, _, _ = context(layer, kind=RunKind.daily_refresh, competitors=(OTHER,))
    result = await IngestStage().run(ctx)
    assert {call[2] for call in layer.ingest.calls} == {"incremental"}
    assert result.notices[0].code is RunNoticeCode.competitor_unavailable
    assert layer.quota.caps[QuotaBucket.main] == 80


async def test_prefetch_and_own_analytics_enablement() -> None:
    layer = FakeLayer()
    ctx, _, _ = context(layer)
    prefetch = await PrefetchOwnChannelStage().run(ctx)
    assert prefetch.status == "succeeded" and prefetch.artifact.videos == 1
    # the channel may only be located during onboarding, so a missing one skips at run time
    without_channel = await PrefetchOwnChannelStage().run(context(layer, own=None)[0])
    assert without_channel.status == "skipped"
    assert not OwnAnalyticsStage().enabled(ctx.snapshot)
    assert OwnAnalyticsStage().enabled(context(layer, oauth=True)[0].snapshot)


class SpendingIngest:
    """spends a fixed amount per channel through the real quota manager, pausing so stages interleave."""

    def __init__(self, quota, units: int, release: asyncio.Event) -> None:
        self.quota = quota
        self.units = units
        self.release = release
        self.spent = asyncio.Event()

    async def ingest_channel(self, channel_id, role, ctx, *, mode, languages=()):
        await self.quota.reserve(QuotaBucket.main, self.units, ctx, "videos.list")
        self.spent.set()
        await self.release.wait()
        return ChannelIngestResult(channel_id=channel_id, channel=channel_record(channel_id))


async def test_parallel_stages_charge_only_their_own_spend(redis, redis_prefix) -> None:
    # daily refresh with 120 run units: ingest and niche spend 40 each at the same time
    quota = manager(redis, redis_prefix, daily_refresh_run_units=200)
    run_ledger = RunBudgetLedger(
        RunBudget(wall_s=600, youtube_units=120, search_calls=5, llm_usd=1, tavily_credits=1)
    )
    release = asyncio.Event()

    def stage_context(name: str, spender: SpendingIngest) -> RunContext:
        layer = FakeLayer()
        layer.quota = quota
        layer.ingest = spender
        ctx, _, _ = context(layer, kind=RunKind.daily_refresh, competitors=())
        return replace(ctx, run_id=run_id, stage=name, budget=run_ledger.for_stage(StageBudget()))

    run_id = uuid.uuid4()
    spenders = [SpendingIngest(quota, 40, release), SpendingIngest(quota, 40, release)]
    ingest_ctx = stage_context("ingest", spenders[0])
    other_ctx = stage_context("niche_discovery", spenders[1])
    ingest = asyncio.create_task(IngestStage().run(ingest_ctx))
    other = asyncio.create_task(IngestStage().run(other_ctx))
    await asyncio.wait_for(asyncio.gather(*(spender.spent.wait() for spender in spenders)), timeout=5)
    release.set()
    first, second = await asyncio.gather(ingest, other)
    assert first.artifact.youtube_units == 40 and second.artifact.youtube_units == 40
    assert run_ledger.spent(BudgetKind.youtube_units) == 80
    # the redis cap is the run's 120 units, not the remainder counted twice
    with pytest.raises(QuotaExhausted):
        await quota.reserve(QuotaBucket.main, 41, youtube_context(ingest_ctx), "videos.list")
