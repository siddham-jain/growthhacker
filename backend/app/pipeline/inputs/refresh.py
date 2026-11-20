from typing import Any

from app.core.tenancy import TenantScope
from app.domain.analytics import VideoMetrics
from app.pipeline.contracts import RunContext
from app.pipeline.inputs.common import ProfileMissing, analysis_request_of, profile_and_questions
from app.pipeline.stages.analytics import ANALYTICS_RESULT
from app.services.analytics.inputs import AnalyticsInputSource
from app.services.analytics.result import AnalyticsResult
from app.services.news.repository import NewsSignalStore
from app.services.refresh.inputs import ReferenceCandidate, RefreshInputs


def widest_metrics(result: AnalyticsResult) -> dict[str, VideoMetrics]:
    # metrics are keyed by comparison age; the latest age (day 7) wins where a video has both
    merged: dict[str, VideoMetrics] = {}
    for age_days in sorted(result.metrics):
        merged.update(result.metrics[age_days])
    return merged


class RunRefreshInputs:
    """09's frozen inputs: this run's 06 artifact, 03 profile, 05 matches and 08's live signals."""

    def __init__(self, container: Any, analytics_inputs: AnalyticsInputSource) -> None:
        self._container = container
        self._analytics_inputs = analytics_inputs

    async def load(self, ctx: RunContext[Any]) -> RefreshInputs:
        snapshot = ctx.snapshot
        scope = TenantScope(workspace_id=ctx.workspace_id, client_id=ctx.client_id)
        profile, _ = await profile_and_questions(self._container, scope)
        if profile is None:
            raise ProfileMissing("the client has no business profile; finish onboarding first")
        own = snapshot.own_channel_id
        analytics = await ctx.artifacts.get(ANALYTICS_RESULT)
        inputs = await self._analytics_inputs.load(analysis_request_of(snapshot, ctx.as_of))
        async with self._container.uow(read_only=True) as uow:
            signals = await NewsSignalStore(
                uow.session, scope, self._container.settings.policy.retention_days
            ).active_for_client(ctx.as_of)
        metrics = widest_metrics(analytics)
        videos = {video.video_id: video for video in inputs.videos}
        roles = {scoped.channel.channel_id: scoped.role for scoped in inputs.channels}
        accepted: dict[str, list[str]] = {}
        for match in inputs.topic_matches:
            if match.decision == "accepted":
                accepted.setdefault(match.video_id, []).append(match.topic_ref_id)
        own_videos = [video for video in inputs.videos if own is not None and video.channel_id == own]
        own_ids = {video.video_id for video in own_videos}
        pool = [
            ReferenceCandidate(
                video=videos[video_id],
                metrics=metric,
                role=roles[metric.channel_id],
                matched_topic_ids=sorted(accepted.get(video_id, [])),
            )
            for video_id, metric in sorted(metrics.items())
            if video_id in videos and video_id not in own_ids and metric.channel_id in roles
        ]
        return RefreshInputs(
            client_id=ctx.client_id,
            run_id=ctx.run_id,
            as_of=ctx.as_of,
            window_days=snapshot.window_days,
            channel_verified=own is not None,
            oauth_connected=snapshot.oauth_connected,
            profile=profile,
            topic_refs=snapshot.topic_refs,
            own_videos=own_videos,
            own_metrics=[metric for video_id, metric in sorted(metrics.items()) if video_id in own_ids],
            channel_baselines=[baseline for baseline in analytics.baselines if baseline.channel_id == own],
            topic_matches=[match for match in inputs.topic_matches if match.video_id in own_ids],
            news_signals=signals,
            reference_pool=pool,
            snapshots={video_id: list(inputs.snapshots.get(video_id, [])) for video_id in sorted(own_ids)},
        )
