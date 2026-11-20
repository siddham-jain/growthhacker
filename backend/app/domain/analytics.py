from datetime import date
from enum import StrEnum
from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from app.domain.common import (
    ChannelId,
    ChannelRole,
    DirectnessLevel,
    DomainModel,
    FormatSegment,
    Ratio,
    SubscriberBand,
    UnitInterval,
    VideoId,
    WindowDays,
)


class AgeMethod(StrEnum):
    observed = "observed"
    interpolated = "interpolated"
    owner_analytics = "owner_analytics"
    age_band_proxy = "age_band_proxy"
    pending = "pending"
    unavailable = "unavailable"


TRUE_AGE_METHODS: frozenset[AgeMethod] = frozenset(
    {AgeMethod.observed, AgeMethod.interpolated, AgeMethod.owner_analytics}
)
VALUELESS_AGE_METHODS: frozenset[AgeMethod] = frozenset({AgeMethod.pending, AgeMethod.unavailable})


class OutlierTier(StrEnum):
    strong_outlier = "strong_outlier"
    outlier = "outlier"
    breakout = "breakout"
    normal = "normal"
    below = "below"
    insufficient = "insufficient"


class MetricFlag(StrEnum):
    likes_hidden = "likes_hidden"
    comments_disabled = "comments_disabled"
    count_decreased = "count_decreased"
    count_frozen = "count_frozen"
    too_new = "too_new"
    low_views_for_rates = "low_views_for_rates"
    duplicate_of = "duplicate_of"
    cross_channel_duplicate = "cross_channel_duplicate"
    live_or_premiere = "live_or_premiere"
    shorts_counting_regime_pre_2025_03_31 = "shorts_counting_regime_pre_2025_03_31"
    published_at_changed = "published_at_changed"
    title_changed = "title_changed"
    thumbnail_changed = "thumbnail_changed"
    unavailable_now = "unavailable_now"
    views_without_engagement = "views_without_engagement"
    non_trailing_baseline = "non_trailing_baseline"
    widened_lookback = "widened_lookback"
    degenerate_frame = "degenerate_frame"
    tiny_baseline = "tiny_baseline"


class IntentClass(StrEnum):
    cost = "cost"
    location = "location"
    booking = "booking"
    doctor = "doctor"
    efficacy = "efficacy"
    how_to = "how_to"
    availability = "availability"
    comparison = "comparison"


class ViewsAtAgeSummary(DomainModel):
    # the compact shape carried on reference videos, packs and events
    age_days: int = Field(ge=0)
    views: int | None
    method: AgeMethod
    label: str

    @model_validator(mode="after")
    def _views_match_method(self) -> "ViewsAtAgeSummary":
        if (self.views is None) != (self.method in VALUELESS_AGE_METHODS):
            raise ValueError("views must be null exactly when method is pending or unavailable")
        return self


class ViewsAtAge(DomainModel):
    video_id: VideoId
    age_days: int = Field(ge=0)
    views: int | None
    likes: int | None = None
    comments: int | None = None
    method: AgeMethod
    observed_age_days: float | None = None
    bracket_lo_at: AwareDatetime | None = None
    bracket_hi_at: AwareDatetime | None = None
    bracket_spread: float | None = None
    source: Literal["data_api_snapshot", "analytics_api"]
    label: str
    as_of: AwareDatetime
    source_oldest_at: AwareDatetime

    def summary(self) -> ViewsAtAgeSummary:
        return ViewsAtAgeSummary(
            age_days=self.age_days, views=self.views, method=self.method, label=self.label
        )


class Velocity(DomainModel):
    views_per_day: float
    from_at: AwareDatetime
    to_at: AwareDatetime
    window_hours: float


class OutlierAssessment(DomainModel):
    metric: Literal["views_at_age"] = "views_at_age"
    age_days: int
    method: AgeMethod
    value: int
    baseline_median: float
    baseline_n: int
    baseline_id: str
    ratio: Ratio
    log_robust_z: float
    tier: OutlierTier
    is_outlier: bool
    suppressed_reason: str | None = None


class VideoMetrics(DomainModel):
    video_id: VideoId
    channel_id: ChannelId
    as_of: AwareDatetime
    segment: FormatSegment
    segment_evidence: dict[str, bool] = {}
    format_key: str
    age_anchor_at: AwareDatetime
    age_days: float
    views_now: int
    likes_now: int | None
    comments_now: int | None
    views_at_age: dict[int, ViewsAtAge]
    comparison_age_days: int
    views_per_day_lifetime: float
    velocity_recent: Velocity | None = None
    velocity_7d: Velocity | None = None
    engagement_rate: float | None = None
    like_rate: float | None = None
    comment_rate: float | None = None
    engagement_basis: Literal["likes+comments", "likes_only", "comments_only", "none"]
    outlier: OutlierAssessment | None
    duplicate_group_id: str | None = None
    flags: list[MetricFlag] = []
    metrics_version: str


class ChannelBaseline(DomainModel):
    baseline_id: str
    channel_id: ChannelId
    segment: FormatSegment
    metric: Literal["views_at_age", "like_rate", "engagement_rate", "velocity"]
    age_days: int | None
    method: AgeMethod
    age_band: tuple[float, float] | None = None
    as_of: AwareDatetime
    anchor_video_id: VideoId | None = None
    median: float
    mean: float
    trimmed_mean_10: float
    p25: float
    p75: float
    mad_log10: float
    n: int
    video_ids: list[VideoId]
    fallback_level: Literal["trailing", "non_trailing", "widened", "none"]
    sufficient: bool


class TitleTraits(DomainModel):
    char_len: int
    char_len_band: str
    word_count: int
    is_question: bool
    has_number: bool
    has_listicle_number: bool
    is_how_to: bool
    has_year: bool
    year_is_current_or_next: bool
    has_brackets: bool
    has_parentheses: bool
    is_first_person: bool
    has_superlative: bool
    caps_ratio: float | None
    caps_band: str | None
    all_caps_word_count: int
    has_emoji: bool
    emoji_count: int
    separator: Literal["none", "colon", "pipe", "dash", "multiple"]
    has_price_cost: bool
    has_buyer_question_phrasing: bool
    mentions_doctor: bool
    script: Literal["latin", "devanagari", "mixed", "other"]
    has_client_location: bool
    has_niche_location: bool


class TimingTraits(DomainModel):
    client_timezone: str
    publish_weekday: int = Field(ge=0, le=6)
    publish_hour_local: int = Field(ge=0, le=23)
    daypart: Literal["night", "morning", "afternoon", "evening", "late"]
    is_weekend: bool


class ThumbnailTraits(DomainModel):
    # simple visual traits only; no face or object recognition
    video_id: VideoId
    source_key: Literal["maxres", "standard", "high", "medium", "default"]
    source_w: int
    source_h: int
    content_sha256: str
    letterbox_cropped: bool
    pillarbox_detected: bool
    text_present: bool
    word_count: int | None
    word_count_band: str | None
    text_box_count: int
    text_area_ratio: UnitInterval
    largest_text_height_ratio: UnitInterval
    brand_text_only: bool
    script_detected: Literal["latin", "devanagari", "mixed", "none", "unknown"]
    ocr_engine: str
    ocr_model: str
    ocr_confidence_mean: float | None
    brightness: UnitInterval
    brightness_band: str
    rms_contrast: float
    contrast_band: str
    colorfulness: float
    colorfulness_band: str
    dominant_colour_count: int
    traits_version: str
    computed_at: AwareDatetime


class DescriptionTraits(DomainModel):
    char_len: int
    links_count: int
    link_domains: list[str]
    has_external_link: bool
    has_own_site_link: bool
    has_shortener_link: bool
    has_booking_cta: bool
    has_phone: bool
    has_whatsapp: bool
    has_chapters: bool
    chapter_count: int
    chapter_titles: list[str]
    hashtag_count: int
    hashtags_ignored: bool
    has_paid_disclosure_text: bool
    mentioned_handles: list[str]
    collab_markers: list[str]
    has_client_site_link: bool
    first_two_lines_have_client_keyword: bool


class TopicTrait(DomainModel):
    topic_ref_id: str
    kind: str
    score: UnitInterval
    review_flag: bool
    directness: DirectnessLevel | None = None


class VideoTraits(DomainModel):
    video_id: VideoId
    traits_version: str
    segment: FormatSegment
    length_band: str
    format_key: str
    title: TitleTraits
    description: DescriptionTraits
    thumbnail: ThumbnailTraits | None
    topics: list[TopicTrait] = []
    timing: TimingTraits


class CommentSignals(DomainModel):
    video_id: VideoId
    as_of: AwareDatetime
    comment_count_api: int | None
    sampled_n: int
    eligible_n: int
    sample_coverage: float | None
    question_n: int
    question_share: float | None
    intent_counts: dict[IntentClass, int]
    buyer_intent_n: int
    buyer_intent_share: float | None
    unanswered_question_n: int
    unanswered_question_share: float | None
    owner_reply_visible_n: int
    lexicon_version: str
    sufficient: bool
    matched_buyer_questions: list[tuple[str, int]] = []
    excerpts: list[str] = []


class LiftScope(StrEnum):
    competitor_set = "competitor_set"
    niche = "niche"
    own = "own"
    channel = "channel"


class TraitLift(DomainModel):
    scope: LiftScope
    window_days: WindowDays
    as_of: AwareDatetime
    comparison_age_days: int
    comparison_method: AgeMethod
    trait_key: str
    trait_value: str
    interaction: tuple[str, str] | None = None
    n_with: int
    n_without: int
    outliers_with: int
    outliers_without: int
    outlier_rate_with: UnitInterval
    outlier_rate_without: UnitInterval
    rate_with_wilson: tuple[float, float]
    lift: float | None
    lift_smoothed: float
    lift_ci: tuple[float, float] | None
    channel_balanced_lift: float | None
    p_value: float
    q_value: float
    outlier_channels_with: int
    max_channel_share: UnitInterval
    ratio_quantiles_with: tuple[float, float, float]
    reference_video_ids: list[VideoId]
    status: Literal[
        "supported", "exploratory", "insufficient_support", "no_contrast", "dominated", "redundant"
    ]
    redundant_with: str | None = None


class GapCell(DomainModel):
    n_videos: int
    n_outliers: int
    best_ratio: float | None
    latest_published_at: AwareDatetime | None
    best_directness: DirectnessLevel | None = None


class GapMatrix(DomainModel):
    # counts only; 07 classifies gaps from these cells
    window_days: WindowDays
    as_of: AwareDatetime
    cells: dict[str, dict[ChannelId, GapCell]]


class CollabMention(DomainModel):
    video_id: VideoId
    handle_or_name: str
    kind: Literal["mention", "disclosed_paid"]


class MedianAtAge(DomainModel):
    value: int
    method: AgeMethod
    n: int


class ChannelScorecard(DomainModel):
    # evidence sheet of raw per-channel stats; no composite score, no ranking
    channel_id: ChannelId
    client_id: str
    role: ChannelRole
    window_days: WindowDays
    as_of: AwareDatetime
    uploads_in_window: int
    uploads_per_week: float
    gap_cv: float | None
    longest_gap_days: float | None
    days_since_last_upload: float | None
    segment_mix: dict[FormatSegment, float]
    median_views_at_age: dict[FormatSegment, MedianAtAge]
    median_ratio: float | None
    outlier_hit_rate: tuple[int, int]
    outlier_hit_rate_wilson: tuple[float, float]
    median_engagement_rate: float | None
    topic_coverage: dict[str, GapCell]
    top_traits: list[TraitLift] = []
    paid_placement_share: tuple[int, int] | None = None
    collaborations: list[CollabMention] = []
    subscriber_count: int | None
    subscriber_band: SubscriberBand
    data_sufficiency: dict[str, bool] = {}


class VideoPerf(DomainModel):
    video_id: VideoId
    published_at: AwareDatetime
    segment: FormatSegment
    format_key: str
    ratio: float | None
    tier: OutlierTier
    method: AgeMethod
    topic_ref_ids: list[str] = []


class SegmentPerf(DomainModel):
    n: int
    median_ratio: float | None
    iqr: tuple[float, float] | None


class TraitPerf(DomainModel):
    median_ratio: float | None
    n: int
    usage_share: UnitInterval


class ConsistencyStats(DomainModel):
    uploads_per_week: float
    gap_cv: float | None
    hit_rate_at_or_above_baseline: UnitInterval | None
    log_ratio_iqr: float | None


class OwnConversionMetrics(DomainModel):
    period: tuple[date, date]
    subscribers_gained_by_video: dict[VideoId, int]
    subscribers_gained_per_1k_views: dict[VideoId, float]
    traffic_source_share: dict[VideoId, dict[str, float]]
    top_search_terms: dict[VideoId, list[tuple[str, int]]]
    source_label: Literal["YouTube Analytics API (owner)"] = "YouTube Analytics API (owner)"


class DirectnessHit(DomainModel):
    video_id: VideoId
    level: DirectnessLevel


class ConversionLens(DomainModel):
    # kept as a separate block; never blended with view metrics
    answer_directness: dict[str, list[DirectnessHit]]
    comment_intent: dict[VideoId, CommentSignals]
    description_cta_coverage: dict[str, tuple[int, int]]
    own_channel: OwnConversionMetrics | None = None


class OwnPerformanceReport(DomainModel):
    client_id: str
    channel_id: ChannelId
    window_days: WindowDays
    as_of: AwareDatetime
    comparison_age_days: int
    method_mix: dict[AgeMethod, int]
    videos: list[VideoPerf]
    median_ratio_window: float | None
    median_ratio_prev_window: float | None
    best: list[VideoId]
    worst: list[VideoId]
    by_format: dict[str, SegmentPerf]
    by_topic: dict[str, SegmentPerf]
    by_trait: dict[str, TraitPerf]
    consistency: ConsistencyStats
    baselines: list[ChannelBaseline]
    conversion: ConversionLens
    sufficiency_messages: list[str] = []


class TrendingItem(DomainModel):
    video_id: VideoId
    channel_id: ChannelId
    ratio: float | None
    method: AgeMethod
    velocity_ratio: float | None
    views_per_day: float
    topic_ref_ids: list[str] = []
    segment: FormatSegment


class TopicTrend(DomainModel):
    topic_ref_id: str
    uploads_now: int
    uploads_prev: int
    outliers_now: int
    outliers_prev: int


class TrendingReport(DomainModel):
    # niche search plus same-age velocity; the mostPopular chart is never used
    client_id: str
    window_days: WindowDays
    as_of: AwareDatetime
    items: list[TrendingItem]
    rising_topics: list[TopicTrend]
    sufficiency_messages: list[str] = []


class OutlierRecord(DomainModel):
    video_id: VideoId
    channel_id: ChannelId
    scope: LiftScope
    published_at: AwareDatetime
    views_at_age: ViewsAtAgeSummary
    ratio: Ratio
    tier: OutlierTier
    baseline_n: int
    topic_ref_ids: list[str] = []
    segment: FormatSegment
    url: str
