import asyncio
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel

from app.adapters.youtube.errors import InsufficientScope, KeyRejected
from app.adapters.youtube.factory import YouTubeDataLayer
from app.core.registry import stage_registry
from app.core.tenancy import TenantScope
from app.domain.common import ChannelRole
from app.domain.runs import RunKind, RunNotice, RunNoticeCode, RunTrigger, VideosFetchedPayload
from app.domain.youtube import QuotaBucket, QuotaPriority
from app.pipeline.contracts import BudgetKind, RunConfigSnapshot, RunContext, StagePolicy, StageResult
from app.pipeline.stages.website_intel import LOCATED_BUSINESS
from app.ports.artifacts import ArtifactKey
from app.services.youtube import messages
from app.services.youtube.context import YouTubeRequestContext, request_context
from app.services.youtube.ingest import ChannelIngestResult
from app.services.youtube.snapshots import CollectReport

DATA_LAYER_PORT = "youtube_data_layer"
# channels fetched at once; the gateway caps concurrent requests itself
CHANNEL_CONCURRENCY = 4


class ChannelIngestSummary(BaseModel):
    channel_id: str
    role: ChannelRole
    status: str
    title: str | None = None
    videos: int = 0
    new_videos: int = 0
    pages: int = 0
    stats_refreshed: int = 0
    flags: list[str] = []
    message: str | None = None


class IngestArtifact(BaseModel):
    mode: str
    channels: list[ChannelIngestSummary]
    quota_limited: bool = False
    youtube_units: int = 0
    data_as_of: datetime


class OwnChannelPrefetch(BaseModel):
    channel_id: str
    videos: int
    status: str


class OwnAnalyticsPullArtifact(BaseModel):
    connected: bool
    requests: int
    data_through: str | None = None
    channel_days: int = 0
    videos: int = 0
    search_terms: int = 0


INGEST = ArtifactKey(name="youtube_ingest", model=IngestArtifact, schema_version=1, contains_api_data=True)
OWN_PREFETCH = ArtifactKey(
    name="own_channel_prefetch", model=OwnChannelPrefetch, schema_version=1, contains_api_data=True
)
OWN_ANALYTICS = ArtifactKey(
    name="own_analytics_pull", model=OwnAnalyticsPullArtifact, schema_version=1, contains_api_data=True
)


def data_layer(ctx: RunContext[Any]) -> YouTubeDataLayer:
    return ctx.deps.adapter(DATA_LAYER_PORT)


def run_priority(ctx: RunContext[Any]) -> QuotaPriority:
    if ctx.kind is RunKind.daily_refresh and ctx.trigger is RunTrigger.schedule:
        return QuotaPriority.daily_refresh
    return QuotaPriority.interactive


def youtube_context(ctx: RunContext[Any], priority: QuotaPriority | None = None) -> YouTubeRequestContext:
    return request_context(
        priority or run_priority(ctx), client_id=ctx.client_id, run_id=ctx.run_id, budget_stage=ctx.stage
    )


def quota_notice(ctx: RunContext[Any], stage: str, bucket: QuotaBucket, resets_at: datetime) -> RunNotice:
    message = messages.quota_exhausted(bucket, ctx.as_of, resets_at, ctx.snapshot.market.timezone)
    return RunNotice(
        code=RunNoticeCode.quota_limited,
        stage=stage,
        message=message[:200],
        detail={"bucket": bucket.value, "resets_at": resets_at.isoformat()},
    )


def _run_cap(setting: int, counted: int, remaining: float | None) -> int:
    # the counter already holds this run's earlier spend, the ledger's remainder excludes it
    return setting if remaining is None else int(min(setting, counted + remaining))


async def open_run_budget(ctx: RunContext[Any], layer: YouTubeDataLayer) -> dict[QuotaBucket, int]:
    """fixes the run-wide caps once and returns this stage's spend so far, the base for charge_usage."""
    quota = layer.settings.quota
    onboarding = ctx.kind in (RunKind.onboarding, RunKind.analysis)
    units = quota.onboarding_run_units if onboarding else quota.daily_refresh_run_units
    calls = quota.onboarding_run_search_calls if onboarding else quota.daily_refresh_run_search_calls
    counted = await layer.quota.run_usage(ctx.run_id)
    caps = {
        QuotaBucket.main: _run_cap(
            units, counted[QuotaBucket.main], ctx.budget.run_remaining(BudgetKind.youtube_units)
        ),
        QuotaBucket.search: _run_cap(
            calls, counted[QuotaBucket.search], ctx.budget.run_remaining(BudgetKind.search_calls)
        ),
    }
    await layer.quota.open_run_budget(ctx.run_id, caps)
    return await layer.quota.run_usage(ctx.run_id, ctx.stage)


async def charge_usage(
    ctx: RunContext[Any], layer: YouTubeDataLayer, before: Mapping[QuotaBucket, int]
) -> int:
    # only this stage's tally: parallel stages charge their own spend
    after = await layer.quota.run_usage(ctx.run_id, ctx.stage)
    units = after[QuotaBucket.main] - before[QuotaBucket.main]
    calls = after[QuotaBucket.search] - before[QuotaBucket.search]
    if units > 0:
        ctx.budget.charge(BudgetKind.youtube_units, units)
    if calls > 0:
        ctx.budget.charge(BudgetKind.search_calls, calls)
    await layer.ledger.flush()
    return units


def summary(result: ChannelIngestResult, role: ChannelRole) -> ChannelIngestSummary:
    return ChannelIngestSummary(
        channel_id=result.channel_id,
        role=role,
        status=result.status,
        title=result.channel.title if result.channel else None,
        videos=len(result.videos),
        new_videos=len(result.new_video_ids),
        pages=result.pages,
        stats_refreshed=result.stats_refreshed,
        flags=result.flags,
        message=result.message,
    )


@stage_registry.register("ingest")
class IngestStage:
    """own and competitor uploads: full lookback for analysis runs, incremental for daily refreshes."""

    name = "ingest"
    version = 1
    depends_on: tuple[str, ...] = ("snapshot_config",)
    optional_deps: tuple[str, ...] = ()
    produces = INGEST
    policy = StagePolicy(timeout_s=600, soft_timeout_s=300, max_attempts=2)
    weight = 25.0

    def enabled(self, snapshot: RunConfigSnapshot) -> bool:
        return bool(snapshot.own_channel_id or snapshot.competitor_channel_ids)

    def fingerprint_inputs(self, ctx: RunContext[Any]) -> Mapping[str, Any]:
        return {
            "own": ctx.snapshot.own_channel_id,
            "competitors": sorted(ctx.snapshot.competitor_channel_ids),
            "mode": self.mode(ctx),
            "pt_day": ctx.as_of.date().isoformat(),
        }

    @staticmethod
    def mode(ctx: RunContext[Any]) -> str:
        return "incremental" if ctx.kind is RunKind.daily_refresh else "full"

    async def run(self, ctx: RunContext[Any]) -> StageResult[IngestArtifact]:
        layer = data_layer(ctx)
        before = await open_run_budget(ctx, layer)
        request = youtube_context(ctx)
        channels = [(ctx.snapshot.own_channel_id, ChannelRole.own)] if ctx.snapshot.own_channel_id else []
        channels += [
            (channel_id, ChannelRole.competitor) for channel_id in ctx.snapshot.competitor_channel_ids
        ]
        languages = [ctx.snapshot.market.language.split("-")[0]]
        summaries: list[ChannelIngestSummary | None] = [None] * len(channels)
        found: dict[int, RunNotice] = {}
        stopped = False
        done = 0
        gate = asyncio.Semaphore(CHANNEL_CONCURRENCY)

        async def ingest_one(index: int, channel_id: str, role: ChannelRole) -> None:
            nonlocal stopped, done
            async with gate:
                await ctx.checkpoint()
                # a rejected key or an exhausted quota stops channels that have not started yet
                if stopped:
                    summaries[index] = ChannelIngestSummary(
                        channel_id=channel_id, role=role, status="skipped"
                    )
                    return
                try:
                    result = await layer.ingest.ingest_channel(
                        channel_id, role, request, mode=self.mode(ctx), languages=languages
                    )
                except KeyRejected:
                    if not stopped:
                        found[index] = RunNotice(
                            code=RunNoticeCode.data_stale, stage=self.name, message=messages.KEY_REJECTED
                        )
                    stopped = True
                    summaries[index] = ChannelIngestSummary(
                        channel_id=channel_id, role=role, status="unavailable"
                    )
                    return
                summaries[index] = summary(result, role)
                notice = self._notice(ctx, result, role)
                if result.status == "quota_limited" and result.quota_bucket and result.quota_resets_at:
                    if stopped:
                        notice = None
                    stopped = True
                if notice is not None:
                    found[index] = notice
                if result.channel is not None:
                    await ctx.reporter.insight(
                        f"{len(result.videos)} videos from {result.channel.title}",
                        VideosFetchedPayload(
                            channel_id=channel_id,
                            channel_title=result.channel.title,
                            count=len(result.videos),
                            window_days=ctx.window_days,
                        ),
                    )
                done += 1
                await ctx.reporter.progress(done, len(channels), "channels")

        await asyncio.gather(*(ingest_one(index, *channel) for index, channel in enumerate(channels)))
        quota_limited = stopped
        notices = [found[index] for index in sorted(found)]
        units = await charge_usage(ctx, layer, before)
        for notice in notices:
            await ctx.reporter.notice(notice)
        artifact = IngestArtifact(
            mode=self.mode(ctx),
            channels=[item for item in summaries if item is not None],
            quota_limited=quota_limited,
            youtube_units=units,
            data_as_of=layer.clock.now(),
        )
        status = "degraded" if notices else "succeeded"
        counts = {"channels": len(channels), "videos": sum(item.videos for item in artifact.channels)}
        return StageResult(status=status, artifact=artifact, counts=counts, notices=tuple(notices))

    def _notice(
        self, ctx: RunContext[Any], result: ChannelIngestResult, role: ChannelRole
    ) -> RunNotice | None:
        if result.status == "quota_limited" and result.quota_bucket and result.quota_resets_at:
            return quota_notice(ctx, self.name, result.quota_bucket, result.quota_resets_at)
        if result.status == "unavailable":
            return RunNotice(
                code=RunNoticeCode.data_stale,
                stage=self.name,
                message=messages.PARTIAL,
                detail={"channel_id": result.channel_id},
            )
        if result.status in ("not_found", "forbidden") and role is ChannelRole.competitor:
            return RunNotice(
                code=RunNoticeCode.competitor_unavailable,
                stage=self.name,
                message=(result.message or messages.FORBIDDEN)[:200],
                detail={"channel_id": result.channel_id},
            )
        return None


@stage_registry.register("prefetch_own_channel")
class PrefetchOwnChannelStage:
    """warms the shared cache with the own channel's last 50 uploads while the site is crawled."""

    name = "prefetch_own_channel"
    version = 1
    depends_on: tuple[str, ...] = ("resolve_inputs",)
    optional_deps: tuple[str, ...] = ()
    produces = OWN_PREFETCH
    policy = StagePolicy(timeout_s=120, soft_timeout_s=60, max_attempts=2, critical=False)
    weight = 8.0

    def enabled(self, snapshot: RunConfigSnapshot) -> bool:
        # the channel is often only found by resolve_inputs, so the frozen snapshot cannot decide this
        return True

    def fingerprint_inputs(self, ctx: RunContext[Any]) -> Mapping[str, Any]:
        return {"own": ctx.snapshot.own_channel_id, "pt_day": ctx.as_of.date().isoformat()}

    async def run(self, ctx: RunContext[Any]) -> StageResult[OwnChannelPrefetch]:
        located = await ctx.artifacts.get_optional(LOCATED_BUSINESS)
        channel_id = ctx.snapshot.own_channel_id or (located.channel_id if located else None)
        if channel_id is None:
            return StageResult(status="skipped", artifact=None)
        layer = data_layer(ctx)
        before = await open_run_budget(ctx, layer)
        [result] = await layer.ingest.ensure_baseline(
            [channel_id], youtube_context(ctx, QuotaPriority.interactive)
        )
        await charge_usage(ctx, layer, before)
        artifact = OwnChannelPrefetch(channel_id=channel_id, videos=len(result.videos), status=result.status)
        if result.status == "quota_limited" and result.quota_bucket and result.quota_resets_at:
            notice = quota_notice(ctx, self.name, result.quota_bucket, result.quota_resets_at)
            return StageResult(status="degraded", artifact=artifact, notices=(notice,))
        return StageResult(status="succeeded", artifact=artifact, counts={"videos": len(result.videos)})


@stage_registry.register("own_analytics")
class OwnAnalyticsStage:
    """owner-authorised youtube analytics for the conversion lens; only when the client connected oauth."""

    name = "own_analytics"
    version = 1
    depends_on: tuple[str, ...] = ("snapshot_config",)
    optional_deps: tuple[str, ...] = ()
    produces = OWN_ANALYTICS
    policy = StagePolicy(timeout_s=180, soft_timeout_s=120, max_attempts=2, critical=False)
    weight = 4.0

    def enabled(self, snapshot: RunConfigSnapshot) -> bool:
        return snapshot.oauth_connected

    def fingerprint_inputs(self, ctx: RunContext[Any]) -> Mapping[str, Any]:
        return {"client": str(ctx.client_id), "pt_day": ctx.as_of.date().isoformat()}

    async def run(self, ctx: RunContext[Any]) -> StageResult[OwnAnalyticsPullArtifact]:
        layer = data_layer(ctx)
        scope = TenantScope(workspace_id=ctx.workspace_id, client_id=ctx.client_id)
        try:
            pulled = await layer.own_analytics.daily_pull(scope, youtube_context(ctx))
        except InsufficientScope:
            notice = RunNotice(
                code=RunNoticeCode.oauth_revoked,
                stage=self.name,
                message="Please reconnect and allow analytics access.",
            )
            artifact = OwnAnalyticsPullArtifact(connected=False, requests=0)
            return StageResult(status="degraded", artifact=artifact, notices=(notice,))
        finally:
            await layer.ledger.flush()
        artifact = OwnAnalyticsPullArtifact(
            connected=pulled.connected,
            requests=pulled.requests,
            data_through=pulled.data_through.isoformat() if pulled.data_through else None,
            channel_days=pulled.channel_days,
            videos=pulled.videos,
            search_terms=pulled.search_terms,
        )
        if not pulled.connected:
            notice = RunNotice(
                code=RunNoticeCode.oauth_revoked,
                stage=self.name,
                message="Your YouTube connection expired. Reconnect to keep channel analytics.",
            )
            return StageResult(status="degraded", artifact=artifact, notices=(notice,))
        return StageResult(status="succeeded", artifact=artifact, counts={"requests": pulled.requests})


async def snapshot_tick(container: Any) -> CollectReport:
    """every 10 minutes (registered by 11): collect due snapshots on the batchGetStats bucket."""
    layer: YouTubeDataLayer = container.adapter(DATA_LAYER_PORT)
    try:
        return await layer.collector.collect()
    finally:
        await layer.ledger.flush()


async def snapshot_maintenance(container: Any) -> int:
    """daily (registered by 11): drop untracked schedules and thin old snapshot series."""
    layer: YouTubeDataLayer = container.adapter(DATA_LAYER_PORT)
    await layer.scheduler.prune()
    return await layer.compactor.compact_tracked()


async def flush_cache_hits(container: Any, day: date) -> int:
    """nightly (registered by 11): per-client cache-hit counts for the ops report, at 0 units."""
    layer: YouTubeDataLayer = container.adapter(DATA_LAYER_PORT)
    return await layer.cache_hits.flush(day, container.uow)
