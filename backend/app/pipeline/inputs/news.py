from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select

from app.core.db import SessionFactory
from app.core.tenancy import TenantScope
from app.domain.youtube import VideoRecord
from app.models.youtube_cache import YtChannel, YtVideo
from app.models.youtube_derived import YtChannelBaseline
from app.pipeline.contracts import RunConfigSnapshot
from app.pipeline.inputs.common import latest_artifact, profile_and_questions
from app.pipeline.stages.analytics import ANALYTICS_RESULT
from app.services.analytics.storage import video_record
from app.services.competitors.edits import CONFIRMED_STATUSES
from app.services.competitors.repository import CompetitorRepository
from app.services.news.context import NewsClientContext

OWN_VIDEO_LOOKBACK_DAYS = 365
OWN_VIDEO_LIMIT = 200


class SqlChannelBaselines:
    """06's stored views-at-age medians (any format segment) and 02's cached subscriber counts."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def views_at_age(self, channel_id: str, age_days: int, as_of: datetime) -> float | None:
        statement = (
            select(YtChannelBaseline.median)
            .where(
                YtChannelBaseline.channel_id == channel_id,
                YtChannelBaseline.metric == "views_at_age",
                YtChannelBaseline.age_days == age_days,
                YtChannelBaseline.sufficient.is_(True),
                YtChannelBaseline.as_of <= as_of,
            )
            .order_by(YtChannelBaseline.as_of.desc(), YtChannelBaseline.n.desc())
            .limit(1)
        )
        async with self._session_factory() as session:
            return (await session.execute(statement)).scalar_one_or_none()

    async def subscriber_count(self, channel_id: str) -> int | None:
        async with self._session_factory() as session:
            channel = await session.get(YtChannel, channel_id)
        return None if channel is None or channel.hidden_subscriber_count else channel.subscriber_count


class TableNewsContextLoader:
    """08's client context: 03 profile and questions, 04 confirmed rivals, 02 own uploads, 06's last ratios."""

    def __init__(self, container: Any) -> None:
        self._container = container

    async def load(self, client_id: UUID, snapshot: RunConfigSnapshot) -> NewsClientContext | None:
        scope = TenantScope(workspace_id=snapshot.workspace_id, client_id=client_id)
        profile, questions = await profile_and_questions(self._container, scope)
        if profile is None:
            return None
        async with self._container.uow(read_only=True) as uow:
            candidates = await CompetitorRepository(uow.session, scope).load_candidates()
        own = snapshot.own_channel_id
        own_videos: list[VideoRecord] = []
        subscribers = None
        if own is not None:
            own_videos = await self._own_videos(own)
            subscribers = await SqlChannelBaselines(self._container.session_factory).subscriber_count(own)
        return NewsClientContext(
            workspace_id=snapshot.workspace_id,
            client_id=client_id,
            profile=profile,
            buyer_questions=questions,
            competitors=[candidate for candidate in candidates if candidate.status in CONFIRMED_STATUSES],
            own_channel_id=own,
            own_videos=own_videos,
            own_day7_ratios=await self._own_ratios(scope, own) if own else {},
            own_subscriber_count=subscribers,
            window_days=snapshot.window_days,
        )

    async def _own_videos(self, channel_id: str) -> list[VideoRecord]:
        since = self._container.clock.now() - timedelta(days=OWN_VIDEO_LOOKBACK_DAYS)
        statement = (
            select(YtVideo)
            .where(YtVideo.channel_id == channel_id, YtVideo.published_at >= since)
            .order_by(YtVideo.published_at.desc())
            .limit(OWN_VIDEO_LIMIT)
        )
        async with self._container.session_factory() as session:
            rows = (await session.execute(statement)).scalars().all()
        videos = []
        for row in rows:
            try:
                videos.append(VideoRecord.model_validate(video_record(row)))
            except ValidationError:
                continue
        return videos

    async def _own_ratios(self, scope: TenantScope, channel_id: str) -> dict[str, float]:
        # news runs beside ingest, so the ratios come from the client's previous analytics
        analytics = await latest_artifact(self._container, scope, ANALYTICS_RESULT)
        if analytics is None:
            return {}
        ratios: dict[str, float] = {}
        for age_days in sorted(analytics.metrics):
            for video_id, metric in analytics.metrics[age_days].items():
                if (
                    metric.channel_id == channel_id
                    and metric.outlier is not None
                    and metric.outlier.age_days == 7
                ):
                    ratios[video_id] = float(metric.outlier.ratio)
        return ratios
