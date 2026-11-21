"""loads the channel landscape inputs from the shared cache and the client's own analytics rows."""

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from app.core.tenancy import TenantScope
from app.domain.analytics import ChannelLandscape
from app.domain.common import ChannelRole
from app.models.own_analytics import OwnAnalyticsDaily
from app.models.youtube_cache import YtChannel, YtChannelSnapshot, YtVideo
from app.services.analytics.channel_overview import (
    GROWTH_DAYS,
    WINDOW_DAYS,
    ChannelFacts,
    ChannelInput,
    HistoryPoint,
    OwnerDay,
    channel_landscape,
)
from app.services.analytics.settings import RateSettings
from app.services.youtube.store import row_to_video


async def load_landscape(
    session_factory: Any,
    scope: TenantScope,
    own_id: str | None,
    competitor_ids: list[str],
    as_of: datetime,
    rates: RateSettings,
) -> ChannelLandscape:
    ids = list(dict.fromkeys([*([own_id] if own_id else []), *competitor_ids]))
    since_videos = as_of - timedelta(days=2 * WINDOW_DAYS)
    since_growth = as_of - timedelta(days=GROWTH_DAYS)
    async with session_factory() as session:
        channels = {
            row.channel_id: row
            for row in (await session.execute(select(YtChannel).where(YtChannel.channel_id.in_(ids))))
            .scalars()
            .all()
        }
        videos: dict[str, list] = defaultdict(list)
        for row in (
            await session.execute(
                select(YtVideo).where(
                    YtVideo.channel_id.in_(ids),
                    YtVideo.effective_published_at >= since_videos,
                    YtVideo.availability == "available",
                )
            )
        ).scalars():
            videos[row.channel_id].append(row_to_video(row))
        oldest = dict(
            (
                await session.execute(
                    select(YtVideo.channel_id, func.min(YtVideo.effective_published_at))
                    .where(YtVideo.channel_id.in_(ids))
                    .group_by(YtVideo.channel_id)
                )
            ).all()
        )
        history: dict[str, list[HistoryPoint]] = defaultdict(list)
        for row in (
            await session.execute(
                select(YtChannelSnapshot).where(
                    YtChannelSnapshot.channel_id.in_(ids), YtChannelSnapshot.captured_at >= since_growth
                )
            )
        ).scalars():
            history[row.channel_id].append(
                HistoryPoint(row.captured_at, row.subscriber_count, row.view_count)
            )
        owner_days = []
        if own_id:
            owner_days = [
                OwnerDay(row.day, row.views, row.subscribers_gained, row.subscribers_lost)
                for row in (
                    await session.execute(
                        select(OwnAnalyticsDaily).where(
                            OwnAnalyticsDaily.workspace_id == scope.workspace_id,
                            OwnAnalyticsDaily.client_id == scope.client_id,
                            OwnAnalyticsDaily.video_id == "",
                            OwnAnalyticsDaily.day >= since_growth.date(),
                        )
                    )
                ).scalars()
            ]

    def channel_input(channel_id: str, role: ChannelRole) -> ChannelInput | None:
        row = channels.get(channel_id)
        if row is None:
            return None
        return ChannelInput(
            facts=ChannelFacts(
                channel_id=channel_id,
                title=row.title or channel_id,
                published_at=row.published_at,
                subscribers=None if row.hidden_subscriber_count else row.subscriber_count,
                total_views=row.view_count,
                total_videos=row.video_count,
            ),
            role=role,
            videos=videos[channel_id],
            oldest_upload_at=oldest.get(channel_id),
            history=history[channel_id],
            owner_days=owner_days if role is ChannelRole.own else (),
        )

    own = channel_input(own_id, ChannelRole.own) if own_id else None
    competitors = [
        channel
        for channel_id in competitor_ids
        if channel_id != own_id and (channel := channel_input(channel_id, ChannelRole.competitor))
    ]
    return channel_landscape(own, competitors, as_of, rates)
