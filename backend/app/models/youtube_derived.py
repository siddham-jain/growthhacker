from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import REAL, BigInteger, ForeignKey, Index, SmallInteger, text
from sqlalchemy.dialects.postgresql import NUMRANGE, Range
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    Base,
    Zone,
    check_in,
    jsonb_object,
    schema_version_column,
    text_array,
)

AGE_METHODS = ("observed", "interpolated", "owner_analytics", "age_band_proxy", "pending", "unavailable")
FORMAT_SEGMENTS = ("short_form", "long_form", "live")
VIEWS_AT_AGE_SOURCES = ("data_api_snapshot", "analytics_api")


class YtVideoAgeValue(Base):
    __tablename__ = "yt_video_age_values"
    __zone__ = Zone.shared
    __table_args__ = (
        check_in("method", AGE_METHODS),
        check_in("source", VIEWS_AT_AGE_SOURCES),
    )

    video_id: Mapped[str] = mapped_column(
        ForeignKey("yt_videos.video_id", ondelete="CASCADE"), primary_key=True
    )
    age_days: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    source: Mapped[str] = mapped_column(primary_key=True)
    views: Mapped[int | None] = mapped_column(BigInteger)
    likes: Mapped[int | None] = mapped_column(BigInteger)
    comments: Mapped[int | None] = mapped_column(BigInteger)
    method: Mapped[str]
    observed_age_days: Mapped[float | None] = mapped_column(REAL)
    bracket_lo_at: Mapped[datetime | None]
    bracket_hi_at: Mapped[datetime | None]
    bracket_spread: Mapped[float | None] = mapped_column(REAL)
    as_of: Mapped[datetime]
    source_oldest_at: Mapped[datetime] = mapped_column(index=True)


class YtVideoMetric(Base):
    __tablename__ = "yt_video_metrics"
    __zone__ = Zone.shared
    __table_args__ = (
        check_in("segment", FORMAT_SEGMENTS),
        Index("ix_yt_video_metrics_channel_id_segment", "channel_id", "segment"),
    )

    video_id: Mapped[str] = mapped_column(
        ForeignKey("yt_videos.video_id", ondelete="CASCADE"), primary_key=True
    )
    channel_id: Mapped[str] = mapped_column(ForeignKey("yt_channels.channel_id", ondelete="CASCADE"))
    as_of: Mapped[datetime]
    segment: Mapped[str]
    segment_evidence: Mapped[dict[str, Any]] = jsonb_object()
    format_key: Mapped[str]
    age_anchor_at: Mapped[datetime]
    age_days: Mapped[float] = mapped_column(REAL)
    comparison_age_days: Mapped[int | None] = mapped_column(SmallInteger)
    views_now: Mapped[int] = mapped_column(BigInteger)
    likes_now: Mapped[int | None] = mapped_column(BigInteger)
    comments_now: Mapped[int | None] = mapped_column(BigInteger)
    views_per_day_lifetime: Mapped[float] = mapped_column(REAL)
    velocity_recent: Mapped[dict[str, Any] | None]
    velocity_7d: Mapped[dict[str, Any] | None]
    engagement_rate: Mapped[float | None] = mapped_column(REAL)
    like_rate: Mapped[float | None] = mapped_column(REAL)
    comment_rate: Mapped[float | None] = mapped_column(REAL)
    engagement_basis: Mapped[str]
    duplicate_group_id: Mapped[str | None]
    flags: Mapped[list[str]] = text_array()
    metrics_version: Mapped[str]
    source_oldest_at: Mapped[datetime] = mapped_column(index=True)


class YtChannelBaseline(Base):
    __tablename__ = "yt_channel_baselines"
    __zone__ = Zone.shared
    __table_args__ = (
        check_in("segment", FORMAT_SEGMENTS),
        check_in("method", AGE_METHODS),
        Index(
            "ix_yt_channel_baselines_channel_id_segment_metric_as_of",
            "channel_id",
            "segment",
            "metric",
            "age_days",
            "method",
            text("as_of DESC"),
        ),
    )

    baseline_id: Mapped[str] = mapped_column(primary_key=True)
    channel_id: Mapped[str] = mapped_column(ForeignKey("yt_channels.channel_id", ondelete="CASCADE"))
    segment: Mapped[str]
    metric: Mapped[str]
    age_days: Mapped[int | None] = mapped_column(SmallInteger)
    method: Mapped[str]
    age_band: Mapped[Range[Decimal] | None] = mapped_column(NUMRANGE())
    anchor_video_id: Mapped[str | None] = mapped_column(
        ForeignKey("yt_videos.video_id", ondelete="CASCADE"), index=True
    )
    as_of: Mapped[datetime]
    median: Mapped[float] = mapped_column(REAL)
    mean: Mapped[float] = mapped_column(REAL)
    trimmed_mean_10: Mapped[float] = mapped_column(REAL)
    p25: Mapped[float] = mapped_column(REAL)
    p75: Mapped[float] = mapped_column(REAL)
    mad_log10: Mapped[float] = mapped_column(REAL)
    n: Mapped[int]
    video_ids: Mapped[list[str]] = text_array()
    fallback_level: Mapped[str]
    sufficient: Mapped[bool]
    source_oldest_at: Mapped[datetime] = mapped_column(index=True)


class YtVideoOutlier(Base):
    __tablename__ = "yt_video_outliers"
    __zone__ = Zone.shared
    __table_args__ = (
        check_in("method", AGE_METHODS),
        Index("ix_yt_video_outliers_tier_as_of_date", "tier", "as_of_date"),
    )

    video_id: Mapped[str] = mapped_column(
        ForeignKey("yt_videos.video_id", ondelete="CASCADE"), primary_key=True
    )
    as_of_date: Mapped[date] = mapped_column(primary_key=True)
    age_days: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    method: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[int] = mapped_column(BigInteger)
    baseline_id: Mapped[str]
    baseline_median: Mapped[float] = mapped_column(REAL)
    baseline_n: Mapped[int]
    ratio: Mapped[float] = mapped_column(REAL)
    log_robust_z: Mapped[float] = mapped_column(REAL)
    tier: Mapped[str]
    is_outlier: Mapped[bool]
    suppressed_reason: Mapped[str | None]
    source_oldest_at: Mapped[datetime] = mapped_column(index=True)


class YtVideoTrait(Base):
    __tablename__ = "yt_video_traits"
    __zone__ = Zone.shared
    __table_args__ = (
        Index(
            "ix_yt_video_traits_traits",
            "traits",
            postgresql_using="gin",
            postgresql_ops={"traits": "jsonb_path_ops"},
        ),
    )

    video_id: Mapped[str] = mapped_column(
        ForeignKey("yt_videos.video_id", ondelete="CASCADE"), primary_key=True
    )
    traits: Mapped[dict[str, Any]] = jsonb_object()
    traits_schema_version: Mapped[int] = schema_version_column()
    traits_version: Mapped[str]
    title_hash: Mapped[str]
    description_hash: Mapped[str]
    computed_at: Mapped[datetime]
    source_oldest_at: Mapped[datetime] = mapped_column(index=True)


class YtThumbnailTrait(Base):
    __tablename__ = "yt_thumbnail_traits"
    __zone__ = Zone.shared

    video_id: Mapped[str] = mapped_column(
        ForeignKey("yt_videos.video_id", ondelete="CASCADE"), primary_key=True
    )
    thumbnail_url: Mapped[str]
    image_etag: Mapped[str | None]
    source_key: Mapped[str]
    source_w: Mapped[int] = mapped_column(SmallInteger)
    source_h: Mapped[int] = mapped_column(SmallInteger)
    content_sha256: Mapped[str] = mapped_column(index=True)
    letterbox_cropped: Mapped[bool]
    pillarbox_detected: Mapped[bool]
    text_present: Mapped[bool]
    word_count: Mapped[int | None] = mapped_column(SmallInteger)
    word_count_band: Mapped[str | None]
    text_box_count: Mapped[int] = mapped_column(SmallInteger)
    text_area_ratio: Mapped[float] = mapped_column(REAL)
    largest_text_height_ratio: Mapped[float] = mapped_column(REAL)
    brand_text_only: Mapped[bool]
    script_detected: Mapped[str]
    ocr_engine: Mapped[str]
    ocr_model: Mapped[str]
    ocr_confidence_mean: Mapped[float | None] = mapped_column(REAL)
    brightness: Mapped[float] = mapped_column(REAL)
    brightness_band: Mapped[str]
    rms_contrast: Mapped[float] = mapped_column(REAL)
    contrast_band: Mapped[str]
    colorfulness: Mapped[float] = mapped_column(REAL)
    colorfulness_band: Mapped[str]
    dominant_colour_count: Mapped[int] = mapped_column(SmallInteger)
    traits_version: Mapped[str]
    computed_at: Mapped[datetime]
    source_verified_at: Mapped[datetime] = mapped_column(index=True)


class YtChannelRecurringText(Base):
    __tablename__ = "yt_channel_recurring_text"
    __zone__ = Zone.shared

    channel_id: Mapped[str] = mapped_column(
        ForeignKey("yt_channels.channel_id", ondelete="CASCADE"), primary_key=True
    )
    normalized_text: Mapped[str] = mapped_column(primary_key=True)
    grid_cell: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    share: Mapped[float] = mapped_column(REAL)
    as_of: Mapped[datetime]
    source_oldest_at: Mapped[datetime] = mapped_column(index=True)


class YtCommentSignal(Base):
    __tablename__ = "yt_comment_signals"
    __zone__ = Zone.shared

    video_id: Mapped[str] = mapped_column(
        ForeignKey("yt_videos.video_id", ondelete="CASCADE"), primary_key=True
    )
    as_of: Mapped[datetime]
    comment_count_api: Mapped[int | None] = mapped_column(BigInteger)
    sampled_n: Mapped[int]
    eligible_n: Mapped[int]
    sample_coverage: Mapped[float | None] = mapped_column(REAL)
    question_n: Mapped[int]
    question_share: Mapped[float | None] = mapped_column(REAL)
    intent_counts: Mapped[dict[str, Any]] = jsonb_object()
    buyer_intent_n: Mapped[int]
    buyer_intent_share: Mapped[float | None] = mapped_column(REAL)
    unanswered_question_n: Mapped[int]
    unanswered_question_share: Mapped[float | None] = mapped_column(REAL)
    owner_reply_visible_n: Mapped[int]
    lexicon_version: Mapped[str]
    sufficient: Mapped[bool]
    source_oldest_at: Mapped[datetime] = mapped_column(index=True)
