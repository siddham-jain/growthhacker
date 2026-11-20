from datetime import datetime
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.domain.analytics import ThumbnailTraits
from app.domain.common import ChannelId, ChannelRole, DomainModel, VideoId
from app.domain.topics import AnswerDirectness, TopicKind, TopicMatch, TopicRef
from app.domain.youtube import ChannelRecord, CommentRecord, OwnAnalytics, VideoRecord, VideoSnapshot


class ClientContext(DomainModel):
    # client-scoped context; used only for client-context traits and never stored in shared tables
    client_id: str
    timezone: str | None = None
    country: str | None = None
    languages: list[str] = []
    website_domains: list[str] = []
    location_terms: list[str] = []
    keyword_terms: list[str] = []
    niche: str | None = None
    phone_short_codes: list[str] = []


class ScopedChannel(DomainModel):
    channel: ChannelRecord
    role: ChannelRole
    website_domains: list[str] = []


class AnalyticsInputs(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    client: ClientContext
    own_channel_id: ChannelId | None = None
    channels: list[ScopedChannel]
    videos: list[VideoRecord]
    snapshots: dict[VideoId, list[VideoSnapshot]] = {}
    comments: dict[VideoId, list[CommentRecord]] = {}
    topic_refs: list[TopicRef] = []
    topic_matches: list[TopicMatch] = []
    directness: list[AnswerDirectness] = []
    thumbnails: dict[VideoId, ThumbnailTraits] = {}
    own_analytics: OwnAnalytics | None = None

    def visible_at(self, as_of: datetime) -> "AnalyticsInputs":
        # backtests at a frozen as_of must not see anything captured later
        return self.model_copy(
            update={
                "videos": [v for v in self.videos if v.published_at <= as_of],
                "snapshots": {
                    vid: [s for s in snaps if s.captured_at <= as_of] for vid, snaps in self.snapshots.items()
                },
                "comments": {
                    vid: [c for c in items if c.fetched_at <= as_of] for vid, items in self.comments.items()
                },
                "thumbnails": {vid: t for vid, t in self.thumbnails.items() if t.computed_at <= as_of},
            }
        )

    def channel_ids(self, *roles: ChannelRole) -> list[str]:
        return sorted(c.channel.channel_id for c in self.channels if c.role in roles)

    def buyer_questions(self) -> list[TopicRef]:
        return [ref for ref in self.topic_refs if ref.kind is TopicKind.buyer_question]


class AnalysisRequest(DomainModel):
    workspace_id: UUID
    client_id: UUID
    as_of: datetime
    own_channel_id: ChannelId | None = None
    competitor_channel_ids: list[ChannelId] = []
    topic_refs: list[TopicRef] = []
    timezone: str | None = None
    country: str | None = None
    language: str | None = None
    oauth_connected: bool = False
    lookback_days: int = 730


class AnalyticsInputSource(Protocol):
    async def load(self, request: AnalysisRequest) -> AnalyticsInputs: ...
