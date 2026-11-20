from datetime import date
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field

from app.domain.common import ChannelId, DomainModel, UnitInterval, VideoFormat, VideoId


class ChannelRef(DomainModel):
    kind: Literal["channel_id", "handle", "username", "custom_url", "video", "playlist", "unknown"]
    value: str
    raw: str


class ChannelRecord(DomainModel):
    channel_id: ChannelId
    title: str
    handle: str | None
    description: str
    country: str | None
    default_language: str | None
    published_at: AwareDatetime
    uploads_playlist_id: str | None
    subscriber_count: int | None
    hidden_subscriber_count: bool
    view_count: int
    video_count: int
    keywords: list[str] = []
    thumbnail_url: str | None
    made_for_kids: bool | None = None
    availability: Literal["available", "unavailable"] = "available"
    unavailable_at: AwareDatetime | None = None
    uploads_90d: int = 0
    last_upload_at: AwareDatetime | None = None
    recent_upload_ids: list[VideoId] = Field(default=[], max_length=50)
    cadence_flags: list[str] = []
    metadata_fetched_at: AwareDatetime
    stats_fetched_at: AwareDatetime


class LocalizedText(DomainModel):
    title: str
    description: str


class ThumbnailRef(DomainModel):
    url: str
    width: int | None = None
    height: int | None = None


class RegionRestriction(DomainModel):
    allowed: list[str] | None = None
    blocked: list[str] | None = None


class VideoRecord(DomainModel):
    video_id: VideoId
    channel_id: ChannelId
    channel_title: str
    published_at: AwareDatetime
    effective_published_at: AwareDatetime
    live_broadcast_content: Literal["none", "live", "upcoming"] = "none"
    live_actual_start_at: AwareDatetime | None = None
    live_actual_end_at: AwareDatetime | None = None
    live_scheduled_start_at: AwareDatetime | None = None
    title: str
    description: str
    tags: list[str] = []
    content_hash: str
    description_hash: str
    default_language: str | None = None
    default_audio_language: str | None = None
    localizations: dict[str, LocalizedText] = {}
    duration_s: int | None
    embed_width: int | None = None
    embed_height: int | None = None
    aspect: Literal["vertical", "square", "horizontal", "unknown"] = "unknown"
    format: VideoFormat
    format_basis: list[str] = []
    format_confidence: Literal["high", "medium", "low"]
    has_captions: bool = False
    licensed_content: bool = False
    age_restricted: bool = False
    region_restriction: RegionRestriction | None = None
    made_for_kids: bool = False
    privacy_status: Literal["public", "unlisted", "private"] = "public"
    upload_status: str = "processed"
    embeddable: bool = True
    public_stats_viewable: bool = True
    has_paid_product_placement: bool | None = None
    thumbnails: dict[str, ThumbnailRef] = {}
    thumbnail_url: str | None = None
    comments_state: Literal["enabled", "disabled", "unknown"] = "unknown"
    likes_visible: bool = True
    view_count: int
    like_count: int | None = None
    comment_count: int | None = None
    availability: Literal["available", "missing", "removed"] = "available"
    first_missing_at: AwareDatetime | None = None
    ingest_source: Literal["uploads", "search", "manual", "recommendation_ref"]
    quality_flags: list[str] = []
    metadata_fetched_at: AwareDatetime

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"


class VideoSnapshot(DomainModel):
    video_id: VideoId
    slot_at: AwareDatetime
    captured_at: AwareDatetime
    age_hours: float = Field(ge=0.0)
    view_count: int
    like_count: int | None = None
    comment_count: int | None = None
    source: Literal["batch_stats", "videos_list", "backtest_freeze"]
    counting_regime: str


class CommentRecord(DomainModel):
    # redacted text only; author identity is never stored
    comment_id: str
    video_id: VideoId
    text: str
    like_count: int
    reply_count: int
    published_at: AwareDatetime
    relevance_rank: int
    is_channel_owner_reply: bool = False
    owner_replied: bool = False
    fetched_at: AwareDatetime


class OwnershipLabel(StrEnum):
    verified = "verified"
    likely = "likely"
    uncertain = "uncertain"
    rejected = "rejected"


class OwnershipSignal(DomainModel):
    name: str
    weight: float
    detail: str
    source_url: str | None = None


class OwnershipVerdict(DomainModel):
    label: OwnershipLabel
    method: Literal["oauth", "link", "user"] | None
    score: UnitInterval
    signals: list[OwnershipSignal]
    llm_reason: str | None = None


class ChannelResolution(DomainModel):
    input: ChannelRef
    channel: ChannelRecord | None
    method: Literal["id", "handle", "username", "video", "playlist", "custom_url_search", "none"]
    candidates: list[ChannelRecord] = []
    ownership: OwnershipVerdict | None = None
    needs_user_confirmation: bool
    message: str | None = None


class SearchQuery(DomainModel):
    q: str
    type: Literal["video", "channel"]
    published_after: date | None = None
    published_before: date | None = None
    order: Literal["relevance", "date", "viewCount", "rating"] = "relevance"
    region_code: str | None = None
    relevance_language: str | None = None
    video_duration: Literal["any", "short", "medium", "long"] = "any"
    max_results: int = Field(default=50, ge=1, le=50)
    page: int = Field(default=0, ge=0)


class SearchHit(DomainModel):
    kind: Literal["video", "channel"]
    id: str
    channel_id: ChannelId
    rank: int


class SearchPage(DomainModel):
    query_key: str
    items: list[SearchHit]
    next_page_token: str | None = None
    fetched_at: AwareDatetime
    expires_at: AwareDatetime
    from_cache: bool


class QuotaBucket(StrEnum):
    main = "main"
    search = "search"
    batch_stats = "batch_stats"
    analytics = "analytics"


class QuotaPriority(StrEnum):
    interactive = "interactive"
    daily_refresh = "daily_refresh"
    snapshot = "snapshot"
    background = "background"


class QuotaUsage(DomainModel):
    bucket: QuotaBucket
    endpoint: str
    units: int
    client_id: UUID | None
    run_id: UUID | None
    priority: QuotaPriority
    scope: Literal["shared", "client", "evaluation"]
    cache_outcome: Literal["miss", "coalesced"]
    http_status: int
    reason: str | None = None


class OAuthConnectionStatus(DomainModel):
    status: Literal["active", "revoked", "invalid_grant", "expired_testing"]
    channel_id: ChannelId
    scopes: list[str]
    last_verified_at: AwareDatetime


class OwnDailyMetrics(DomainModel):
    day: date
    views: int
    engaged_views: int | None = None
    estimated_minutes_watched: int
    average_view_duration_s: float
    subscribers_gained: int
    subscribers_lost: int
    likes: int
    comments: int
    shares: int


class OwnVideoMetrics(DomainModel):
    video_id: VideoId
    period_start: date
    period_end: date
    creator_content_type: str | None = None
    views: int
    engaged_views: int | None = None
    estimated_minutes_watched: int
    average_view_duration_s: float
    average_view_percentage: float
    subscribers_gained: int
    subscribers_lost: int
    likes: int
    comments: int
    shares: int


class OwnVideoDayViews(DomainModel):
    video_id: VideoId
    day: date
    views: int


class TrafficSourceRow(DomainModel):
    video_id: VideoId | None
    period_start: date
    period_end: date
    source_type: str
    views: int
    estimated_minutes_watched: int


class SearchTermRow(DomainModel):
    video_id: VideoId | None
    period_start: date
    period_end: date
    term: str
    views: int
    estimated_minutes_watched: int


class OwnAnalytics(DomainModel):
    # labelled "YouTube Analytics (owner-authorised)"; never merged with public data
    connection: OAuthConnectionStatus
    data_through: date
    channel_daily: list[OwnDailyMetrics] = []
    video_period: list[OwnVideoMetrics] = []
    video_daily_views: list[OwnVideoDayViews] = []
    traffic_sources: list[TrafficSourceRow] = []
    search_terms: list[SearchTermRow] = []
