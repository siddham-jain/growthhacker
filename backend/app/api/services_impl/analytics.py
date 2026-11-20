from collections import Counter
from datetime import timedelta
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import func, select

from app.api.errors import BadRequest, NotFound
from app.api.pagination import Page, PageRequest
from app.api.schemas.results import (
    CadencePoint,
    CompetitorDetail,
    CompetitorSheet,
    GapMatrixData,
    GapRow,
    OutlierRow,
    OwnPerformanceData,
    OwnSide,
    TopicCoverage,
    TopicSummary,
    TrendingData,
    VideoRow,
)
from app.api.services import ANALYTICS, AnalyticsScope, api_service_registry
from app.api.services_impl.common import (
    ArtifactNotStored,
    ServiceBase,
    channel_refs,
    sort_items,
    summary_or_bare,
    video_summaries,
)
from app.api.services_impl.own_figures import own_scorecard
from app.api.services_impl.profile import ProfileEdits, ProfileMissing
from app.core.container import Container
from app.core.tenancy import TenantScope
from app.domain.analytics import ChannelScorecard, LiftScope, OutlierRecord, TraitLift
from app.domain.common import ChannelRole
from app.domain.topics import TopicKind
from app.models.runs import PipelineRun
from app.models.topics import TopicReviewItem
from app.models.youtube_cache import YtVideo
from app.pipeline.contracts import RunConfigSnapshot
from app.pipeline.stages.analytics import ANALYTICS_RESULT
from app.services.analytics.result import AnalyticsResult, WindowAnalytics
from app.services.analytics.settings import AnalyticsEngineSettings

SCOPE_KEYS: dict[str, LiftScope] = {
    "own": LiftScope.own,
    "competitors": LiftScope.competitor_set,
    "niche": LiftScope.niche,
}
ROLE_ORDER = {
    ChannelRole.competitor: 0,
    ChannelRole.niche: 1,
    ChannelRole.benchmark: 2,
    ChannelRole.creator: 3,
    ChannelRole.own: 4,
}
SHEET_SORTS = {
    "name": lambda sheet: (sheet.channel.title or sheet.channel.channel_id).casefold(),
    "uploads_per_week": lambda sheet: sheet.scorecard.uploads_per_week,
    "median_ratio": lambda sheet: sheet.scorecard.median_ratio,
    "outliers": lambda sheet: sheet.scorecard.outlier_hit_rate[0],
    "days_since_last_upload": lambda sheet: sheet.scorecard.days_since_last_upload,
}
VIDEO_SORTS = {
    "outlier_ratio": lambda row: row.metrics.outlier.ratio if row.metrics.outlier else None,
    "published_at": lambda row: row.metrics.age_anchor_at,
    "views_now": lambda row: row.metrics.views_now,
}
SHEET_OUTLIERS = 3
DETAIL_OUTLIERS = 10


def check_sort(sort: str, allowed: dict[str, Any]) -> str:
    if sort.removeprefix("-") not in allowed:
        raise BadRequest(f"sort by one of: {', '.join(sorted(allowed))} (prefix '-' for descending)")
    return sort


class AnalyticsReadService(ServiceBase):
    """06's published analytics artifact, wrapped with display names; numbers pass through unchanged."""

    def __init__(self, container: Container) -> None:
        super().__init__(container)
        self.edits = ProfileEdits(container)

    async def _window(
        self, scope: TenantScope, run_id: UUID, window_days: int
    ) -> tuple[AnalyticsResult, WindowAnalytics]:
        result = await self.artifact(scope, run_id, ANALYTICS_RESULT)
        window = result.windows.get(window_days)
        if window is None:
            raise ArtifactNotStored(f"the published run has no {window_days}-day analytics")
        return result, window

    async def _snapshot(self, run_id: UUID) -> RunConfigSnapshot | None:
        async with self.session_factory() as session:
            run = await session.get(PipelineRun, run_id)
        if run is None or not run.config_snapshot:
            return None
        try:
            return RunConfigSnapshot.model_validate(run.config_snapshot)
        except ValidationError:
            return None

    async def _topic_labels(self, run_id: UUID) -> dict[str, str]:
        snapshot = await self._snapshot(run_id)
        return {ref.id: ref.label for ref in snapshot.topic_refs} if snapshot else {}

    async def _outlier_rows(self, records: list[OutlierRecord]) -> list[OutlierRow]:
        summaries = await video_summaries(self.session_factory, [record.video_id for record in records])
        return [
            OutlierRow(video=summary_or_bare(summaries, record.video_id, record.channel_id), outlier=record)
            for record in records
        ]

    async def own_performance(self, scope: TenantScope, run_id: UUID, window_days: int) -> OwnPerformanceData:
        _, window = await self._window(scope, run_id, window_days)
        report = window.own_report
        summaries = await video_summaries(
            self.session_factory, [video.video_id for video in report.videos] if report else []
        )
        return OwnPerformanceData(
            youtube_public=report,
            youtube_analytics=window.conversion.own_channel,
            website=await self._website_section(scope),
            videos=summaries,
        )

    async def _website_section(self, scope: TenantScope) -> dict[str, Any] | None:
        # from the business profile, labelled as website data and never combined with youtube numbers
        try:
            async with self.session_factory() as session:
                profile = (await self.edits.load(session, scope)).effective
        except ProfileMissing:
            return None
        return {
            "source_label": "Website (not from YouTube)",
            "conversion_actions": [
                {"id": action.id, "type": action.type, "label": action.label, "target": action.target}
                for action in profile.conversion_actions
            ],
            "offerings": len(profile.offerings),
        }

    async def competitors(
        self, scope: TenantScope, run_id: UUID, window_days: int, sort: str | None
    ) -> list[CompetitorSheet]:
        order = check_sort(sort or "name", SHEET_SORTS)
        _, window = await self._window(scope, run_id, window_days)
        cards = [card for card in window.scorecards if card.role is not ChannelRole.own]
        refs = await channel_refs(self.session_factory, [card.channel_id for card in cards])
        outliers = window.outliers.get(LiftScope.niche.value, []) + window.outliers.get(
            LiftScope.competitor_set.value, []
        )
        by_channel: dict[str, list[OutlierRecord]] = {}
        for record in {record.video_id: record for record in outliers}.values():
            by_channel.setdefault(record.channel_id, []).append(record)
        sheets = []
        for card in cards:
            top = sorted(by_channel.get(card.channel_id, []), key=lambda r: -r.ratio)[:SHEET_OUTLIERS]
            sheets.append(
                CompetitorSheet(
                    channel=refs[card.channel_id], scorecard=card, top_outliers=await self._outlier_rows(top)
                )
            )
        ordered = sort_items(sheets, order, SHEET_SORTS)
        # the chosen sort applies inside each role group: competitors first, then niche and benchmarks
        return sorted(ordered, key=lambda sheet: ROLE_ORDER.get(sheet.scorecard.role, 9))

    async def competitor(
        self, scope: TenantScope, run_id: UUID, window_days: int, channel_id: str
    ) -> CompetitorDetail:
        result, window = await self._window(scope, run_id, window_days)
        card = self._scorecard(window, channel_id)
        refs = await channel_refs(self.session_factory, [channel_id])
        records = [
            record
            for record in window.outliers.get(LiftScope.niche.value, [])
            + window.outliers.get(LiftScope.competitor_set.value, [])
            if record.channel_id == channel_id
        ]
        unique = sorted({r.video_id: r for r in records}.values(), key=lambda r: -r.ratio)[:DETAIL_OUTLIERS]
        labels = await self._topic_labels(run_id)
        own_id = next((c.channel_id for c in window.scorecards if c.role is ChannelRole.own), None)
        cells = window.gap_matrix.cells
        coverage = [
            TopicCoverage(
                topic_ref_id=ref_id,
                label=labels.get(ref_id, ref_id),
                competitor=cell,
                client=cells.get(ref_id, {}).get(own_id) if own_id else None,
            )
            for ref_id, cell in sorted(card.topic_coverage.items())
        ]
        return CompetitorDetail(
            channel=refs[channel_id],
            scorecard=card,
            own=await self._own_side(run_id, window, result, window_days),
            cadence_series=await self._cadence(channel_id, result, window_days),
            top_outliers=await self._outlier_rows(unique),
            trait_lift=card.top_traits,
            topic_coverage=coverage,
            collaborations=card.collaborations,
        )

    async def _own_side(
        self, run_id: UUID, window: WindowAnalytics, result: AnalyticsResult, window_days: int
    ) -> OwnSide | None:
        snapshot = await self._snapshot(run_id)
        channel_id = window.own_report.channel_id if window.own_report else None
        channel_id = channel_id or (snapshot.own_channel_id if snapshot else None)
        if channel_id is None:
            return None
        async with self.session_factory() as session:
            published = list(
                (
                    await session.execute(
                        select(YtVideo.published_at).where(
                            YtVideo.channel_id == channel_id, YtVideo.published_at <= result.as_of
                        )
                    )
                ).scalars()
            )
        ref = (await channel_refs(self.session_factory, [channel_id], lambda _: True))[channel_id]
        card = own_scorecard(
            result,
            window,
            channel_id,
            ref.subscriber_count,
            published,
            AnalyticsEngineSettings.from_settings(self.settings),
        )
        return OwnSide(
            channel=ref, scorecard=card, cadence_series=await self._cadence(channel_id, result, window_days)
        )

    @staticmethod
    def _scorecard(window: WindowAnalytics, channel_id: str) -> ChannelScorecard:
        card = next((card for card in window.scorecards if card.channel_id == channel_id), None)
        if card is None:
            raise NotFound("this channel is not part of the published analysis")
        return card

    async def _cadence(
        self, channel_id: str, result: AnalyticsResult, window_days: int
    ) -> list[CadencePoint]:
        # upload counts per iso week, from published dates only
        since = result.as_of - timedelta(days=window_days)
        week = func.date_trunc("week", YtVideo.published_at)
        async with self.session_factory() as session:
            rows = (
                await session.execute(
                    select(week, func.count())
                    .where(
                        YtVideo.channel_id == channel_id,
                        YtVideo.published_at >= since,
                        YtVideo.published_at <= result.as_of,
                    )
                    .group_by(week)
                    .order_by(week)
                )
            ).all()
        return [CadencePoint(week_start=start.date().isoformat(), uploads=count) for start, count in rows]

    async def competitor_videos(
        self,
        scope: TenantScope,
        run_id: UUID,
        window_days: int,
        channel_id: str,
        sort: str,
        topic_ref_id: str | None,
        page: PageRequest,
    ) -> Page[VideoRow]:
        order = check_sort(sort, VIDEO_SORTS)
        result, window = await self._window(scope, run_id, window_days)
        self._scorecard(window, channel_id)
        metrics = result.metrics.get(window.comparison_age_days, {})
        since = result.as_of - timedelta(days=window_days)
        chosen = [m for m in metrics.values() if m.channel_id == channel_id and m.age_anchor_at >= since]
        if topic_ref_id is not None:
            chosen = [
                m
                for m in chosen
                if (traits := result.traits.get(m.video_id)) is not None
                and any(topic.topic_ref_id == topic_ref_id for topic in traits.topics)
            ]
        summaries = await video_summaries(self.session_factory, [m.video_id for m in chosen])
        rows = [
            VideoRow(
                video=summary_or_bare(summaries, m.video_id, m.channel_id),
                metrics=m,
                traits=result.traits.get(m.video_id),
            )
            for m in chosen
        ]
        filters = {
            "run": str(run_id),
            "window": window_days,
            "channel": channel_id,
            "sort": order,
            "topic": topic_ref_id,
        }
        return self.paginate(sort_items(rows, order, VIDEO_SORTS), page, filters)

    async def trending(self, scope: TenantScope, run_id: UUID, window_days: int) -> TrendingData:
        _, window = await self._window(scope, run_id, window_days)
        report = window.trending
        return TrendingData(
            report=report,
            videos=await video_summaries(self.session_factory, [item.video_id for item in report.items]),
            topics=await self._topic_labels(run_id),
        )

    async def outliers(
        self,
        scope: TenantScope,
        run_id: UUID,
        window_days: int,
        analytics_scope: AnalyticsScope,
        min_ratio: float | None,
        page: PageRequest,
    ) -> Page[OutlierRow]:
        _, window = await self._window(scope, run_id, window_days)
        records = window.outliers.get(SCOPE_KEYS[analytics_scope].value, [])
        if min_ratio is not None:
            records = [record for record in records if record.ratio >= min_ratio]
        filters = {
            "run": str(run_id),
            "window": window_days,
            "scope": analytics_scope,
            "min_ratio": min_ratio,
        }
        chunk = self.paginate(records, page, filters)
        return Page(
            items=await self._outlier_rows(chunk.items),
            next_cursor=chunk.next_cursor,
            limit=chunk.limit,
            total=chunk.total,
        )

    async def patterns(
        self, scope: TenantScope, run_id: UUID, window_days: int, analytics_scope: AnalyticsScope
    ) -> list[TraitLift]:
        _, window = await self._window(scope, run_id, window_days)
        return window.lifts.get(SCOPE_KEYS[analytics_scope].value, [])

    async def topics(self, scope: TenantScope, run_id: UUID, window_days: int) -> list[TopicSummary]:
        _, window = await self._window(scope, run_id, window_days)
        snapshot = await self._snapshot(run_id)
        own_id = snapshot.own_channel_id if snapshot else None
        flagged = await self._review_flags(scope)
        refs = snapshot.topic_refs if snapshot else []
        summaries = []
        for ref in refs:
            row = window.gap_matrix.cells.get(ref.id, {})
            others = [cell for channel, cell in row.items() if channel != own_id]
            own = row.get(own_id) if own_id else None
            summaries.append(
                TopicSummary(
                    topic_ref={"id": ref.id, "label": ref.label, "kind": ref.kind.value},
                    own_videos=own.n_videos if own else 0,
                    competitor_videos=sum(cell.n_videos for cell in others),
                    outliers=sum(cell.n_outliers for cell in row.values()),
                    review_flagged=flagged[ref.id],
                )
            )
        return summaries

    async def gaps(self, scope: TenantScope, run_id: UUID, window_days: int) -> GapMatrixData:
        _, window = await self._window(scope, run_id, window_days)
        snapshot = await self._snapshot(run_id)
        refs = (
            [ref for ref in snapshot.topic_refs if ref.kind is TopicKind.buyer_question] if snapshot else []
        )
        tracked = [card for card in window.scorecards if card.role is ChannelRole.competitor]
        own_id = window.own_report.channel_id if window.own_report else None
        own_id = own_id or (snapshot.own_channel_id if snapshot else None)
        names = await channel_refs(self.session_factory, [card.channel_id for card in tracked])
        channels = sorted(names.values(), key=lambda ref: (ref.title or ref.channel_id).casefold())
        cells = window.gap_matrix.cells
        own_channel = None
        if own_id is not None:
            own_channel = (await channel_refs(self.session_factory, [own_id], lambda _: True))[own_id]
        return GapMatrixData(
            rows=[GapRow(topic_ref_id=ref.id, label=ref.label, kind=ref.kind.value) for ref in refs],
            channels=channels,
            cells={
                ref.id: {
                    channel.channel_id: cell
                    for channel in channels
                    if (cell := cells.get(ref.id, {}).get(channel.channel_id)) is not None
                }
                for ref in refs
            },
            own_channel=own_channel,
            own={
                ref.id: cell
                for ref in refs
                if own_id is not None and (cell := cells.get(ref.id, {}).get(own_id)) is not None
            },
        )

    async def _review_flags(self, scope: TenantScope) -> Counter[str]:
        async with self.session_factory() as session:
            rows = (
                await session.execute(
                    select(TopicReviewItem.topic_ref_id, func.count())
                    .where(
                        TopicReviewItem.workspace_id == scope.workspace_id,
                        TopicReviewItem.client_id == scope.client_id,
                        TopicReviewItem.status == "open",
                    )
                    .group_by(TopicReviewItem.topic_ref_id)
                )
            ).all()
        return Counter({ref_id: count for ref_id, count in rows})


@api_service_registry.register(ANALYTICS)
def build_analytics_service(container: Container) -> AnalyticsReadService:
    return AnalyticsReadService(container)
