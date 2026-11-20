import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, ClassVar, Protocol

from app.core.config import Settings
from app.core.registry import artifact_schema_registry, stage_registry
from app.domain.analytics import ThumbnailTraits
from app.domain.common import DomainModel
from app.domain.runs import OutlierFoundPayload, RunNotice, RunNoticeCode, TraitLiftPayload
from app.domain.youtube import VideoRecord
from app.pipeline.contracts import RunConfigSnapshot, RunContext, StagePolicy, StageResult
from app.ports.artifacts import ArtifactKey
from app.services.analytics.engine import AnalyticsEngine
from app.services.analytics.inputs import AnalysisRequest, AnalyticsInputs, AnalyticsInputSource
from app.services.analytics.result import AnalyticsResult, WindowAnalytics
from app.services.analytics.settings import AnalyticsEngineSettings
from app.services.analytics.storage import AnalyticsStore
from app.services.analytics.thumbnails import rapidocr_reader
from app.services.analytics.thumbnails.analyzer import TRAITS_VERSION, ReadThumbnail, ThumbnailTraitBuilder
from app.services.analytics.thumbnails.fetch import ImageSource, ThumbnailFetcher
from app.services.analytics.thumbnails.text import ThumbnailTextReader, brand_tokens_of, recurring_text
from app.services.analytics.traits.title import script_of

INSIGHT_OUTLIERS = 5
INSIGHT_LIFTS = 3
RECURRING_REFRESH_DAYS = 7
# adapter modules that register through adapter_registry when this stage is imported
REGISTERED_ADAPTERS = (rapidocr_reader,)


class ThumbnailEnrichment(DomainModel):
    computed: int
    reused: int
    failed: list[str] = []
    channels: int


ANALYTICS_RESULT: ArtifactKey[AnalyticsResult] = ArtifactKey(
    name="analytics", model=AnalyticsResult, schema_version=1, contains_api_data=True
)
THUMBNAIL_ENRICHMENT: ArtifactKey[ThumbnailEnrichment] = ArtifactKey(
    name="enrich", model=ThumbnailEnrichment, schema_version=1, contains_api_data=False
)
artifact_schema_registry.register(ANALYTICS_RESULT.name, ANALYTICS_RESULT.schema_version, AnalyticsResult)
artifact_schema_registry.register(
    THUMBNAIL_ENRICHMENT.name, THUMBNAIL_ENRICHMENT.schema_version, ThumbnailEnrichment
)


class AnalyticsDeps(Protocol):
    settings: Settings
    analytics_inputs: AnalyticsInputSource
    analytics_store: AnalyticsStore


class EnrichDeps(AnalyticsDeps, Protocol):
    image_source: ImageSource
    thumbnail_text_reader: ThumbnailTextReader


@dataclass(frozen=True, slots=True)
class AnalyticsStageDeps:
    settings: Settings
    analytics_inputs: AnalyticsInputSource
    analytics_store: AnalyticsStore
    image_source: ImageSource
    thumbnail_text_reader: ThumbnailTextReader

    @classmethod
    def from_container(cls, container: Any) -> "AnalyticsStageDeps":
        return cls(
            settings=container.settings,
            analytics_inputs=container.adapter("analytics_inputs"),
            analytics_store=AnalyticsStore(container.session_factory),
            image_source=container.adapter("image_source"),
            thumbnail_text_reader=container.adapter("thumbnail_text_reader"),
        )


def analysis_request(ctx: RunContext[Any]) -> AnalysisRequest:
    snapshot = ctx.snapshot
    return AnalysisRequest(
        workspace_id=snapshot.workspace_id,
        client_id=snapshot.client_id,
        as_of=ctx.as_of,
        own_channel_id=snapshot.own_channel_id,
        competitor_channel_ids=snapshot.competitor_channel_ids,
        topic_refs=snapshot.topic_refs,
        timezone=snapshot.market.timezone,
        country=snapshot.market.country,
        language=snapshot.market.language,
        oauth_connected=snapshot.oauth_connected,
    )


@stage_registry.register("enrich")
class EnrichStage:
    """thumbnail traits for new or changed thumbnails, plus per-channel recurring-text (watermark) stats."""

    name: ClassVar[str] = "enrich"
    version: ClassVar[int] = 1
    depends_on: ClassVar[tuple[str, ...]] = ("ingest",)
    optional_deps: ClassVar[tuple[str, ...]] = ("niche_discovery",)
    produces: ClassVar[ArtifactKey[ThumbnailEnrichment]] = THUMBNAIL_ENRICHMENT
    policy: ClassVar[StagePolicy] = StagePolicy(timeout_s=900, soft_timeout_s=600, critical=False)
    weight: ClassVar[float] = 10.0

    def enabled(self, snapshot: RunConfigSnapshot) -> bool:
        return True

    def fingerprint_inputs(self, ctx: RunContext[EnrichDeps]) -> Mapping[str, Any]:
        return {"thumbnail_traits": TRAITS_VERSION}

    async def run(self, ctx: RunContext[EnrichDeps]) -> StageResult[ThumbnailEnrichment]:
        deps = ctx.deps
        settings = AnalyticsEngineSettings.from_settings(deps.settings)
        inputs = await deps.analytics_inputs.load(analysis_request(ctx))
        builder = ThumbnailTraitBuilder(deps.thumbnail_text_reader, settings.thumbnails)
        fetcher = ThumbnailFetcher(deps.image_source, settings.thumbnails.source_order)
        await ctx.reporter.step("enrich.thumbnails", "Reading thumbnail text and colour")
        computed, reused, failed = 0, 0, []
        channels = sorted({v.channel_id for v in inputs.videos})
        for index, channel_id in enumerate(channels):
            await ctx.checkpoint()
            made, kept, missing = await self._channel(ctx, inputs, channel_id, builder, fetcher, settings)
            computed, reused, failed = computed + made, reused + kept, failed + missing
            await ctx.reporter.progress(index + 1, len(channels), "channels")
        status = "degraded" if failed and not computed else "succeeded"
        return StageResult(
            status=status,
            artifact=ThumbnailEnrichment(
                computed=computed, reused=reused, failed=sorted(failed), channels=len(channels)
            ),
            counts={
                "thumbnails_computed": computed,
                "thumbnails_reused": reused,
                "thumbnails_failed": len(failed),
            },
        )

    async def _channel(
        self,
        ctx: RunContext[EnrichDeps],
        inputs: AnalyticsInputs,
        channel_id: str,
        builder: ThumbnailTraitBuilder,
        fetcher: ThumbnailFetcher,
        settings: AnalyticsEngineSettings,
    ) -> tuple[int, int, list[str]]:
        cfg, store = settings.thumbnails, ctx.deps.analytics_store
        videos = sorted(
            (v for v in inputs.videos if v.channel_id == channel_id and v.thumbnails),
            key=lambda v: (v.published_at, v.video_id),
            reverse=True,
        )
        window_start = ctx.as_of - timedelta(days=max(settings.windows))
        targets = [
            v for v in videos if v.published_at >= window_start and self._needs_traits(v, inputs.thumbnails)
        ]
        recurring = await store.recurring_text(channel_id, ctx.as_of - timedelta(days=RECURRING_REFRESH_DAYS))
        lookback = videos[: cfg.recurring_lookback_thumbnails] if recurring is None else []
        hint = self._devanagari_hint(videos, inputs, cfg.devanagari_title_share)
        reads: dict[str, ReadThumbnail] = {}
        failed: list[str] = []
        for video in {v.video_id: v for v in targets + lookback}.values():
            fetched = await fetcher.fetch_best(video)
            read = await asyncio.to_thread(builder.read, fetched, hint) if fetched else None
            if read is None:
                failed.append(video.video_id)
                continue
            reads[video.video_id] = read
        if recurring is None:
            shares = recurring_text(
                [reads[v.video_id].page for v in lookback if v.video_id in reads],
                cfg.recurring_text_share,
                cfg.recurring_min_thumbnails,
            )
            await store.write_recurring_text(channel_id, shares, ctx.as_of)
            recurring = frozenset(shares)
        scoped = next((c for c in inputs.channels if c.channel.channel_id == channel_id), None)
        brand = brand_tokens_of(scoped.channel.title if scoped else videos[0].channel_title if videos else "")
        traits: list[ThumbnailTraits] = [
            builder.traits(v.video_id, reads[v.video_id], recurring, brand, ctx.as_of)
            for v in targets
            if v.video_id in reads
        ]
        await store.write_thumbnails(
            traits, {t.video_id: reads[t.video_id].fetched.url for t in traits}, ctx.as_of
        )
        reused = sum(1 for v in videos if v.published_at >= window_start) - len(targets)
        return len(traits), reused, [vid for vid in failed if any(v.video_id == vid for v in targets)]

    @staticmethod
    def _needs_traits(video: VideoRecord, existing: Mapping[str, ThumbnailTraits]) -> bool:
        # traits are immutable per thumbnail; 02 flags a swapped thumbnail and a version bump recomputes
        current = existing.get(video.video_id)
        return (
            current is None
            or current.traits_version != TRAITS_VERSION
            or "thumbnail_changed" in video.quality_flags
        )

    @staticmethod
    def _devanagari_hint(videos: list[VideoRecord], inputs: AnalyticsInputs, min_share: float) -> bool:
        if "hi" in inputs.client.languages:
            return True
        scripts = [script_of(v.title) for v in videos]
        return (
            bool(scripts) and sum(s in ("devanagari", "mixed") for s in scripts) / len(scripts) >= min_share
        )


@stage_registry.register("analytics")
class AnalyticsStage:
    """same-age metrics, baselines, outliers, traits, comment signals, patterns and reports for 7/30/90 days."""

    name: ClassVar[str] = "analytics"
    version: ClassVar[int] = 1
    depends_on: ClassVar[tuple[str, ...]] = ("ingest", "topics")
    optional_deps: ClassVar[tuple[str, ...]] = ("enrich", "own_analytics", "niche_discovery")
    produces: ClassVar[ArtifactKey[AnalyticsResult]] = ANALYTICS_RESULT
    policy: ClassVar[StagePolicy] = StagePolicy(timeout_s=120, soft_timeout_s=60)
    weight: ClassVar[float] = 10.0

    def enabled(self, snapshot: RunConfigSnapshot) -> bool:
        return True

    def fingerprint_inputs(self, ctx: RunContext[AnalyticsDeps]) -> Mapping[str, Any]:
        return {
            "settings_hash": AnalyticsEngineSettings.from_settings(ctx.deps.settings).settings_hash(),
            "windows": list(ctx.windows),
        }

    async def run(self, ctx: RunContext[AnalyticsDeps]) -> StageResult[AnalyticsResult]:
        deps = ctx.deps
        settings = AnalyticsEngineSettings.from_settings(deps.settings)
        inputs = await deps.analytics_inputs.load(analysis_request(ctx))
        await ctx.checkpoint()
        result = await asyncio.to_thread(AnalyticsEngine(settings).run, inputs, ctx.as_of)
        await ctx.reporter.step(
            "analytics.baselines_done", f"Built {len(result.baselines)} channel baselines"
        )
        written = await deps.analytics_store.write(result, inputs)
        window = result.windows.get(ctx.window_days) or next(iter(result.windows.values()))
        await self._insights(ctx, inputs, window)
        await ctx.reporter.step("analytics.patterns_done", "Pattern mining finished")
        notices = tuple(
            RunNotice(code=RunNoticeCode.thin_data, stage=self.name, message=message[:200])
            for message in window.sufficiency_messages[:3]
        )
        return StageResult(
            status="succeeded",
            artifact=result,
            counts={
                "videos": len(result.metrics.get(window.comparison_age_days, {})),
                "baselines": len(result.baselines),
                "outliers": sum(len(items) for items in window.outliers.values()),
                **{f"rows_{table}": n for table, n in sorted(written.items())},
            },
            notices=notices,
        )

    @staticmethod
    async def _insights(
        ctx: RunContext[AnalyticsDeps], inputs: AnalyticsInputs, window: WindowAnalytics
    ) -> None:
        titles = {v.video_id: v for v in inputs.videos}
        channels = {c.channel.channel_id: c.channel.title for c in inputs.channels}
        records = sorted(
            {r.video_id: r for items in window.outliers.values() for r in items}.values(),
            key=lambda r: (-r.ratio, r.published_at, r.video_id),
        )
        for record in records[:INSIGHT_OUTLIERS]:
            video = titles[record.video_id]
            await ctx.reporter.insight(
                f"{record.ratio:.1f}x its channel's normal: {video.title}"[:200],
                OutlierFoundPayload(
                    video_id=record.video_id,
                    channel_title=channels.get(record.channel_id, video.channel_title),
                    title=video.title,
                    outlier_ratio=record.ratio,
                    views_at_age=record.views_at_age,
                ),
            )
        supported = [lift for lifts in window.lifts.values() for lift in lifts if lift.status == "supported"]
        for lift in supported[:INSIGHT_LIFTS]:
            await ctx.reporter.insight(
                f"Pattern: {lift.trait_key}={lift.trait_value} ({lift.outliers_with} outliers)"[:200],
                TraitLiftPayload(
                    trait=f"{lift.trait_key}={lift.trait_value}",
                    lift=lift.lift if lift.lift is not None else lift.lift_smoothed,
                    outliers_with_trait=lift.outliers_with,
                    sample=lift.n_with,
                ),
            )
