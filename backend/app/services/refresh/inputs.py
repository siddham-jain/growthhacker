from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime

from app.domain.analytics import ChannelBaseline, VideoMetrics
from app.domain.common import ChannelRole, DomainModel, VideoId, WindowDays
from app.domain.news import NewsSignal
from app.domain.profile import BusinessProfile
from app.domain.refresh import DescriptionRefresh, RefreshImpact
from app.domain.topics import TopicMatch, TopicRef
from app.domain.youtube import OwnVideoDayViews, VideoRecord, VideoSnapshot


class RefreshClientOptions(DomainModel):
    mode: Literal["off", "hygiene_only", "auto"] = "auto"
    excluded_video_ids: list[VideoId] = []
    news_on_top: bool = False
    utm_tagging: bool = False


class ReferenceCandidate(DomainModel):
    # a niche or competitor video that may serve as demand evidence for a keyword
    video: VideoRecord
    metrics: VideoMetrics
    role: ChannelRole
    matched_topic_ids: list[str] = []


class ApplicabilityConfirmation(DomainModel):
    # the owner ticked "this video's content applies to <keyword>", which unlocks L3 wording
    video_id: VideoId
    keyword: str


class StoredRefresh(DomainModel):
    refresh: DescriptionRefresh
    applied_at: date | None = None
    applied_source: Literal["user", "detected"] | None = None
    updated_at: AwareDatetime


class PastImpact(DomainModel):
    trigger_kind: str
    impact: RefreshImpact


class RefreshInputs(DomainModel):
    """everything the refresh stage reads, assembled from upstream artifacts and 09's own tables."""

    client_id: UUID
    run_id: UUID
    as_of: AwareDatetime
    window_days: WindowDays
    channel_verified: bool
    oauth_connected: bool = False
    profile: BusinessProfile
    topic_refs: list[TopicRef] = []
    own_videos: list[VideoRecord] = []
    own_metrics: list[VideoMetrics] = []
    channel_baselines: list[ChannelBaseline] = []
    topic_matches: list[TopicMatch] = []
    news_signals: list[NewsSignal] = []
    reference_pool: list[ReferenceCandidate] = []
    snapshots: dict[str, list[VideoSnapshot]] = {}
    owner_search_terms: dict[str, list[str]] = {}
    owner_search_daily_views: dict[str, list[OwnVideoDayViews]] = {}
    confirmations: list[ApplicabilityConfirmation] = []
    options: RefreshClientOptions = RefreshClientOptions()
    previous_refreshes: list[StoredRefresh] = []
    past_impacts: list[PastImpact] = []
    last_keyword_eval_at: AwareDatetime | None = None
