from datetime import datetime
from typing import Any

from sqlalchemy import (
    REAL,
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    PrimaryKeyConstraint,
    SmallInteger,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import CITEXT, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Zone, check_in, created_at_column, jsonb_object, text_array

CHANNEL_AVAILABILITY = ("available", "unavailable")
VIDEO_AVAILABILITY = ("available", "missing", "removed")
VIDEO_FORMATS = ("short_form", "long_form", "live", "upcoming", "unknown")
LIVE_BROADCAST_CONTENT = ("none", "live", "upcoming")
COMMENTS_STATES = ("enabled", "disabled", "unknown")
INGEST_SOURCES = ("uploads", "search", "manual", "recommendation_ref")
SNAPSHOT_SOURCES = ("batch_stats", "videos_list", "backtest_freeze")
PARTITIONED_TABLES = ("yt_video_snapshots", "yt_channel_snapshots")


class YtChannel(Base):
    __tablename__ = "yt_channels"
    __zone__ = Zone.shared
    __table_args__ = (
        CheckConstraint("channel_id ~ '^UC[0-9A-Za-z_-]{22}$'", name="channel_id_format"),
        check_in("availability", CHANNEL_AVAILABILITY),
        Index("ix_yt_channels_handle", "handle"),
        Index(
            "ix_yt_channels_retention_clock",
            func.least(text("metadata_fetched_at"), text("stats_fetched_at")),
        ),
        Index("ix_yt_channels_uploads_synced_at", "uploads_synced_at"),
    )

    channel_id: Mapped[str] = mapped_column(primary_key=True)
    title: Mapped[str | None]
    handle: Mapped[str | None] = mapped_column(CITEXT())
    custom_url: Mapped[str | None]
    description: Mapped[str | None]
    country: Mapped[str | None]
    default_language: Mapped[str | None]
    published_at: Mapped[datetime | None]
    uploads_playlist_id: Mapped[str | None]
    subscriber_count: Mapped[int | None] = mapped_column(BigInteger)
    hidden_subscriber_count: Mapped[bool] = mapped_column(server_default=text("false"))
    view_count: Mapped[int | None] = mapped_column(BigInteger)
    video_count: Mapped[int | None]
    keywords: Mapped[list[str]] = text_array()
    thumbnail_url: Mapped[str | None]
    made_for_kids: Mapped[bool | None]
    availability: Mapped[str] = mapped_column(server_default=text("'available'"))
    unavailable_at: Mapped[datetime | None]
    uploads_90d: Mapped[int | None]
    last_upload_at: Mapped[datetime | None]
    recent_upload_ids: Mapped[list[str]] = text_array()
    cadence_flags: Mapped[list[str]] = text_array()
    etag: Mapped[str | None]
    metadata_fetched_at: Mapped[datetime]
    stats_fetched_at: Mapped[datetime]
    uploads_synced_at: Mapped[datetime | None]
    uploads_synced_through: Mapped[datetime | None]
    backfill_cursor: Mapped[str | None]
    backfill_horizon: Mapped[datetime | None]
    backfill_complete: Mapped[bool] = mapped_column(server_default=text("false"))
    first_seen_at: Mapped[datetime] = created_at_column()


class YtChannelSnapshot(Base):
    __tablename__ = "yt_channel_snapshots"
    __zone__ = Zone.shared
    __table_args__ = (
        PrimaryKeyConstraint("channel_id", "captured_at"),
        {"postgresql_partition_by": "RANGE (captured_at)"},
    )

    channel_id: Mapped[str] = mapped_column(ForeignKey("yt_channels.channel_id", ondelete="CASCADE"))
    captured_at: Mapped[datetime]
    subscriber_count: Mapped[int | None] = mapped_column(BigInteger)
    view_count: Mapped[int | None] = mapped_column(BigInteger)
    video_count: Mapped[int | None]


class YtChannelResolution(Base):
    __tablename__ = "yt_channel_resolutions"
    __zone__ = Zone.shared
    __table_args__ = (
        PrimaryKeyConstraint("input_kind", "input_normalized"),
        check_in(
            "input_kind", ("handle", "username", "custom_url", "channel_url", "video_url", "channel_id")
        ),
        CheckConstraint("expires_at <= resolved_at + interval '28 days'", name="expires_within_28d"),
    )

    input_kind: Mapped[str]
    input_normalized: Mapped[str]
    channel_id: Mapped[str | None]
    resolved_via: Mapped[str]
    resolved_at: Mapped[datetime]
    expires_at: Mapped[datetime] = mapped_column(index=True)


class YtVideo(Base):
    __tablename__ = "yt_videos"
    __zone__ = Zone.shared
    __table_args__ = (
        CheckConstraint("video_id ~ '^[0-9A-Za-z_-]{11}$'", name="video_id_format"),
        check_in("availability", VIDEO_AVAILABILITY),
        check_in("format", VIDEO_FORMATS),
        check_in("live_broadcast_content", LIVE_BROADCAST_CONTENT, nullable=True),
        check_in("comments_state", COMMENTS_STATES),
        check_in("ingest_source", INGEST_SOURCES),
        Index(
            "ix_yt_videos_channel_id_effective_published_at",
            "channel_id",
            text("effective_published_at DESC"),
        ),
        Index("ix_yt_videos_published_at", text("published_at DESC")),
        Index(
            "ix_yt_videos_retention_clock", func.least(text("metadata_fetched_at"), text("stats_fetched_at"))
        ),
        Index(
            "ix_yt_videos_availability", "availability", postgresql_where=text("availability <> 'available'")
        ),
        Index("ix_yt_videos_format", "format", postgresql_where=text("availability = 'available'")),
    )

    video_id: Mapped[str] = mapped_column(primary_key=True)
    channel_id: Mapped[str] = mapped_column(ForeignKey("yt_channels.channel_id", ondelete="CASCADE"))
    channel_title: Mapped[str | None]
    published_at: Mapped[datetime]
    effective_published_at: Mapped[datetime]
    live_broadcast_content: Mapped[str | None]
    live_actual_start_at: Mapped[datetime | None]
    live_actual_end_at: Mapped[datetime | None]
    live_scheduled_start_at: Mapped[datetime | None]
    title: Mapped[str | None]
    description: Mapped[str | None]
    tags: Mapped[list[str]] = text_array()
    content_hash: Mapped[str | None]
    description_hash: Mapped[str | None]
    default_language: Mapped[str | None]
    default_audio_language: Mapped[str | None]
    localizations: Mapped[dict[str, Any]] = jsonb_object()
    localizations_fetched_at: Mapped[datetime | None]
    duration_s: Mapped[int | None]
    embed_width: Mapped[int | None]
    embed_height: Mapped[int | None]
    aspect: Mapped[float | None] = mapped_column(REAL)
    format: Mapped[str] = mapped_column(server_default=text("'unknown'"))
    format_basis: Mapped[list[str]] = text_array()
    format_confidence: Mapped[float | None] = mapped_column(REAL)
    has_captions: Mapped[bool | None]
    licensed_content: Mapped[bool | None]
    age_restricted: Mapped[bool | None]
    region_restriction: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    made_for_kids: Mapped[bool | None]
    privacy_status: Mapped[str | None]
    upload_status: Mapped[str | None]
    embeddable: Mapped[bool | None]
    public_stats_viewable: Mapped[bool | None]
    has_paid_product_placement: Mapped[bool | None]
    thumbnails: Mapped[dict[str, Any]] = jsonb_object()
    thumbnail_url: Mapped[str | None]
    comments_state: Mapped[str] = mapped_column(server_default=text("'unknown'"))
    likes_visible: Mapped[bool | None]
    view_count: Mapped[int | None] = mapped_column(BigInteger)
    like_count: Mapped[int | None] = mapped_column(BigInteger)
    comment_count: Mapped[int | None] = mapped_column(BigInteger)
    availability: Mapped[str] = mapped_column(server_default=text("'available'"))
    first_missing_at: Mapped[datetime | None]
    ingest_source: Mapped[str]
    quality_flags: Mapped[list[str]] = text_array()
    etag: Mapped[str | None]
    metadata_fetched_at: Mapped[datetime]
    stats_fetched_at: Mapped[datetime]
    first_seen_at: Mapped[datetime] = created_at_column()


class YtVideoSnapshot(Base):
    __tablename__ = "yt_video_snapshots"
    __zone__ = Zone.shared
    __table_args__ = (
        PrimaryKeyConstraint("video_id", "slot_at"),
        check_in("source", SNAPSHOT_SOURCES),
        CheckConstraint("age_hours >= 0", name="age_hours_non_negative"),
        Index("ix_yt_video_snapshots_video_id_age_hours", "video_id", "age_hours"),
        {"postgresql_partition_by": "RANGE (slot_at)"},
    )

    video_id: Mapped[str] = mapped_column(ForeignKey("yt_videos.video_id", ondelete="CASCADE"))
    slot_at: Mapped[datetime]
    captured_at: Mapped[datetime]
    age_hours: Mapped[float] = mapped_column(REAL)
    view_count: Mapped[int] = mapped_column(BigInteger)
    like_count: Mapped[int | None] = mapped_column(BigInteger)
    comment_count: Mapped[int | None] = mapped_column(BigInteger)
    source: Mapped[str]
    counting_regime: Mapped[str] = mapped_column(server_default=text("'standard'"))


class YtSnapshotSchedule(Base):
    __tablename__ = "yt_snapshot_schedule"
    __zone__ = Zone.shared

    video_id: Mapped[str] = mapped_column(
        ForeignKey("yt_videos.video_id", ondelete="CASCADE"), primary_key=True
    )
    next_due_at: Mapped[datetime] = mapped_column(index=True)
    tier: Mapped[str]
    milestone_target_h: Mapped[int | None]
    priority: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
    tracking_started_at: Mapped[datetime]
    first_seen_age_h: Mapped[float | None] = mapped_column(REAL)
    last_captured_at: Mapped[datetime | None]
    failed_attempts: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))


class YtComment(Base):
    __tablename__ = "yt_comments"
    __zone__ = Zone.shared
    __table_args__ = (
        Index("ix_yt_comments_video_id_published_at", "video_id", text("published_at DESC")),
        CheckConstraint("char_length(text_redacted) <= 1500", name="text_redacted_length"),
    )

    comment_id: Mapped[str] = mapped_column(primary_key=True)
    video_id: Mapped[str] = mapped_column(ForeignKey("yt_videos.video_id", ondelete="CASCADE"))
    thread_id: Mapped[str]
    parent_comment_id: Mapped[str | None]
    is_reply: Mapped[bool]
    text_redacted: Mapped[str]
    like_count: Mapped[int | None]
    reply_count: Mapped[int | None]
    relevance_rank: Mapped[int | None]
    is_channel_owner_reply: Mapped[bool] = mapped_column(server_default=text("false"))
    published_at: Mapped[datetime]
    source_updated_at: Mapped[datetime | None]
    fetched_at: Mapped[datetime] = mapped_column(index=True)


class YtCommentFetch(Base):
    __tablename__ = "yt_comment_fetch"
    __zone__ = Zone.shared
    __table_args__ = (check_in("outcome", ("ok", "disabled", "empty", "error")),)

    video_id: Mapped[str] = mapped_column(
        ForeignKey("yt_videos.video_id", ondelete="CASCADE"), primary_key=True
    )
    fetched_at: Mapped[datetime] = mapped_column(index=True)
    outcome: Mapped[str]
    n: Mapped[int] = mapped_column(server_default=text("0"))


class YtSearchCache(Base):
    __tablename__ = "yt_search_cache"
    __zone__ = Zone.shared
    __table_args__ = (
        CheckConstraint("expires_at <= fetched_at + interval '28 days'", name="expires_within_28d"),
    )

    query_key: Mapped[str] = mapped_column(primary_key=True)
    endpoint: Mapped[str]
    params: Mapped[dict[str, Any]] = mapped_column(JSONB)
    total_results: Mapped[int | None]
    next_page_token: Mapped[str | None]
    quota_units: Mapped[int] = mapped_column(server_default=text("0"))
    fetched_at: Mapped[datetime]
    expires_at: Mapped[datetime] = mapped_column(index=True)
    hit_count: Mapped[int] = mapped_column(server_default=text("0"))
    last_hit_at: Mapped[datetime | None]


class YtSearchResult(Base):
    __tablename__ = "yt_search_result"
    __zone__ = Zone.shared
    __table_args__ = (
        PrimaryKeyConstraint("query_key", "rank"),
        check_in("kind", ("video", "channel", "playlist")),
    )

    query_key: Mapped[str] = mapped_column(ForeignKey("yt_search_cache.query_key", ondelete="CASCADE"))
    rank: Mapped[int] = mapped_column(SmallInteger)
    kind: Mapped[str]
    resource_id: Mapped[str] = mapped_column(index=True)
    channel_id: Mapped[str | None]


class YtHttpEtag(Base):
    __tablename__ = "yt_http_etag"
    __zone__ = Zone.shared

    request_key: Mapped[str] = mapped_column(primary_key=True)
    etag: Mapped[str]
    stored_at: Mapped[datetime] = mapped_column(index=True)
