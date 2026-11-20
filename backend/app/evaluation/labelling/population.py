from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import select

from app.core.config import Settings
from app.evaluation.labelling.models import Population, PopulationVideo, UploadPoint
from app.models.tenancy import Client
from app.pipeline.contracts import RunConfigSnapshot
from app.services.analytics.engine import AnalyticsEngine
from app.services.analytics.inputs import AnalysisRequest, AnalyticsInputs
from app.services.analytics.result import AnalyticsResult
from app.services.analytics.settings import AnalyticsEngineSettings

UPLOADS_SHOWN = 30


class PopulationSource(Protocol):
    async def load(self, now: datetime) -> Population: ...


def analysis_request_for(snapshot: RunConfigSnapshot, as_of: datetime) -> AnalysisRequest:
    return AnalysisRequest(
        workspace_id=snapshot.workspace_id,
        client_id=snapshot.client_id,
        as_of=as_of,
        own_channel_id=snapshot.own_channel_id,
        competitor_channel_ids=snapshot.competitor_channel_ids,
        topic_refs=snapshot.topic_refs,
        timezone=snapshot.market.timezone,
        country=snapshot.market.country,
        language=snapshot.market.language,
        oauth_connected=snapshot.oauth_connected,
    )


def comparison_age(settings: AnalyticsEngineSettings, window_days: int) -> int:
    return settings.comparison_age(window_days)


def build_population(
    runs: Sequence[tuple[str, AnalyticsInputs, AnalyticsResult]],
    settings: AnalyticsEngineSettings,
    window_days: int,
    now: datetime,
) -> Population:
    """complete videos (a ratio at the comparison age) across clients; one row per video, clients merged."""
    age = comparison_age(settings, window_days)
    rows: dict[str, dict[str, Any]] = {}
    clients: dict[str, set[str]] = defaultdict(set)
    uploads: dict[str, tuple[UploadPoint, ...]] = {}
    excluded = 0
    for client_id, inputs, result in runs:
        channels = {c.channel.channel_id: c.channel for c in inputs.channels}
        videos = {v.video_id: v for v in inputs.visible_at(now).videos}
        latest = {
            vid: max(snaps, key=lambda s: s.captured_at) for vid, snaps in inputs.snapshots.items() if snaps
        }
        for video_id, metric in sorted(result.metrics.get(age, {}).items()):
            assessment = metric.outlier
            video = videos.get(video_id)
            if assessment is None or video is None:
                excluded += 1
                continue
            clients[video_id].add(client_id)
            channel = channels.get(video.channel_id)
            snapshot = latest.get(video_id)
            oldest = min(video.metadata_fetched_at, snapshot.captured_at if snapshot else now)
            rows[video_id] = {
                "video_id": video_id,
                "channel_id": video.channel_id,
                "channel_title": channel.title if channel else video.channel_title,
                "title": video.title,
                "segment": metric.segment.value,
                "duration_s": video.duration_s,
                "published_at": video.published_at,
                "age_days": round(metric.age_days, 2),
                "views": metric.views_now,
                "ratio": float(assessment.ratio),
                "is_outlier": assessment.is_outlier,
                "subscriber_count": channel.subscriber_count if channel else None,
                "hidden_subscribers": bool(channel.hidden_subscriber_count) if channel else True,
                "thumbnail_url": video.thumbnail_url,
                "oldest_api_at": oldest,
            }
        for channel_id in channels:
            own = sorted(
                (v for v in videos.values() if v.channel_id == channel_id),
                key=lambda v: (v.published_at, v.video_id),
                reverse=True,
            )[:UPLOADS_SHOWN]
            uploads[channel_id] = tuple(
                UploadPoint(
                    video_id=v.video_id,
                    title=v.title,
                    published_at=v.published_at,
                    views=latest[v.video_id].view_count if v.video_id in latest else v.view_count,
                    age_days=round((now - v.published_at).total_seconds() / 86400, 2),
                    segment=v.format.value,
                )
                for v in own
            )
    population = tuple(
        PopulationVideo(**row, client_ids=tuple(sorted(clients[vid]))) for vid, row in sorted(rows.items())
    )
    return Population(
        observed_at=now,
        threshold=settings.outliers.outlier_ratio,
        detector_version=settings.settings_hash(),
        videos=population,
        uploads=uploads,
        excluded_no_ratio=excluded,
    )


class EnginePopulationSource:
    """runs 06's engine at `now` for each client; the label pack shows the same numbers the detector used."""

    def __init__(self, container: Any, client_ids: Sequence[UUID] | None = None) -> None:
        self.container = container
        self.client_ids = list(client_ids or [])

    async def load(self, now: datetime) -> Population:
        settings: Settings = self.container.settings
        engine_settings = AnalyticsEngineSettings.from_settings(settings)
        config_source = self.container.adapter("run_config_source")
        inputs_source = self.container.adapter("analytics_inputs")
        window = settings.evaluation.backtest.window_days
        runs = []
        async with self.container.session_factory() as session:
            statement = select(Client).where(Client.archived_at.is_(None)).order_by(Client.id)
            if self.client_ids:
                statement = statement.where(Client.id.in_(self.client_ids))
            clients = (await session.execute(statement)).scalars().all()
            snapshots = [await config_source.snapshot(session, client, window) for client in clients]
        for snapshot in snapshots:
            inputs = await inputs_source.load(analysis_request_for(snapshot, now))
            result = AnalyticsEngine(engine_settings).run(inputs, now)
            runs.append((str(snapshot.client_id), inputs, result))
        return build_population(runs, engine_settings, window, now)
