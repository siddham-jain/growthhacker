from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, Field

from app.api.schemas.clients import ChannelRef
from app.api.schemas.common import ApiModel
from app.domain.analytics import (
    ChannelScorecard,
    CollabMention,
    GapCell,
    OutlierRecord,
    OwnConversionMetrics,
    OwnPerformanceReport,
    TraitLift,
    TrendingReport,
    VideoMetrics,
    VideoTraits,
)
from app.domain.confidence import Confidence
from app.domain.news import NewsSourceRef
from app.domain.recommendations import (
    CollaborationEvidence,
    CreatorConsistency,
    CreatorPerformance,
    InsufficiencyNotice,
    Recommendation,
    RecommendationFeedback,
    RecType,
    ReferenceVideo,
    StrictRecommendation,
    WhiteSpaceNote,
)
from app.domain.refresh import Advisory, InsertionOp, RefreshEligibility


class VideoSummary(BaseModel):
    # display fields from the public cache; thumbnails are hotlinked from youtube, never proxied
    video_id: str
    channel_id: str
    title: str | None
    channel_title: str | None
    published_at: AwareDatetime | None
    url: str
    thumbnail_url: str


class OwnPerformanceData(BaseModel):
    # the three sources stay separate sibling sections by policy; never merged
    youtube_public: OwnPerformanceReport | None
    youtube_analytics: OwnConversionMetrics | None = None
    website: dict[str, Any] | None = None
    videos: dict[str, VideoSummary] = {}


class VideoRow(BaseModel):
    video: VideoSummary
    metrics: VideoMetrics
    traits: VideoTraits | None = None


class OutlierRow(BaseModel):
    video: VideoSummary
    outlier: OutlierRecord


class CompetitorSheet(BaseModel):
    # an evidence sheet: raw per-channel stats side by side, no composite score or rank (policy, 99 C4)
    channel: ChannelRef
    scorecard: ChannelScorecard
    top_outliers: list[OutlierRow] = Field(default=[], max_length=3)


class CadencePoint(BaseModel):
    week_start: str
    uploads: int


class TopicCoverage(BaseModel):
    topic_ref_id: str
    label: str
    competitor: GapCell
    client: GapCell | None = None


class OwnSide(BaseModel):
    # the client's own channel over the same window, for side-by-side reading; raw stats only
    channel: ChannelRef
    scorecard: ChannelScorecard
    cadence_series: list[CadencePoint] = []


class CompetitorDetail(BaseModel):
    channel: ChannelRef
    scorecard: ChannelScorecard
    own: OwnSide | None = None
    cadence_series: list[CadencePoint] = []
    top_outliers: list[OutlierRow] = Field(default=[], max_length=10)
    trait_lift: list[TraitLift] = []
    topic_coverage: list[TopicCoverage] = []
    collaborations: list[CollabMention] = []


class GapRow(BaseModel):
    topic_ref_id: str
    label: str
    kind: str


class GapMatrixData(BaseModel):
    # counts only: rows are topic refs, columns the tracked channels; no score, no rank
    rows: list[GapRow]
    channels: list[ChannelRef]
    cells: dict[str, dict[str, GapCell]]
    own_channel: ChannelRef | None = None
    own: dict[str, GapCell] = {}


class TrendingData(BaseModel):
    report: TrendingReport
    videos: dict[str, VideoSummary] = {}
    topics: dict[str, str] = {}


class TopicSummary(BaseModel):
    topic_ref: dict[str, Any]
    own_videos: int
    competitor_videos: int
    outliers: int
    review_flagged: int


class RecommendationOut(BaseModel):
    # the contract fields plus extras; ?format=contract returns StrictRecommendation instead
    recommendation: Recommendation
    feedback: RecommendationFeedback | None = None


class ListingFormat(BaseModel):
    # strict contract mode keeps only the assignment's five card types; full mode keeps every surface
    format: Literal["contract", "full"]
    types: list[RecType]
    excluded_types: list[RecType] = []
    note: str | None = None


class RecommendationsData(BaseModel):
    items: list[RecommendationOut] | list[StrictRecommendation]
    meta: ListingFormat | None = None
    next_cursor: str | None = None
    limit: int
    notices: list[InsufficiencyNotice] = []
    white_space: list[WhiteSpaceNote] = []
    suppressed_counts: dict[str, int] = {}


class FeedbackIn(ApiModel):
    status: Literal["saved", "dismissed", "done", "snoozed"] | None = None
    reason: (
        Literal[
            "not_our_business",
            "already_covered",
            "cannot_produce_format",
            "creator_not_suitable",
            "evidence_wrong",
            "other",
        ]
        | None
    ) = None
    linked_video_id: str | None = Field(default=None, pattern=r"^[0-9A-Za-z_-]{11}$")
    note: str | None = Field(default=None, max_length=1000)


class FeedbackOut(BaseModel):
    feedback: RecommendationFeedback


class NewsRef(BaseModel):
    news_id: str
    headline: str
    url: str
    source_domain: str
    published_at: AwareDatetime | None = None


class DemandCheckView(BaseModel):
    status: str
    youtube_results_7d: int | None
    outliers: int | None


class NewsOpportunityView(BaseModel):
    id: str
    news: NewsRef
    recommendation_id: str | None = None
    summary: str | None = None
    relevance: float
    offering_refs: list[str] = []
    demand_check: DemandCheckView
    urgency: Literal["now", "this_week", "evergreen_window"]
    expires_at: AwareDatetime
    reference_count: int
    review_required: bool
    status: Literal["new", "dismissed", "used"]


class EarlySignalView(BaseModel):
    # first-mover news with fewer than 3 reference videos; its own surface, never a card (owner o3)
    id: str
    opportunity_id: str
    key_entity: str
    angle: str
    urgency: str
    expires_at: AwareDatetime
    sources: list[NewsSourceRef]
    reference_videos: list[ReferenceVideo] = []
    confidence: Confidence
    statements: list[str] = []
    window_days: int


class RefreshVideo(BaseModel):
    video_id: str
    title: str | None
    published_at: AwareDatetime | None
    url: str


class RefreshSuggestionView(BaseModel):
    id: str
    video: RefreshVideo
    basis: Literal["keyword", "news", "rule_based"]
    warranted_because: list[str]
    operations: list[InsertionOp]
    advisories: list[Advisory] = []
    inserted_bytes: int
    resulting_bytes: int
    expected_effect: str
    review_required: bool
    original_description_hash: str
    # unified diff against the cached description; empty when the description changed since generation
    preview_diff: str = Field(max_length=20_000)
    description_changed: bool
    status: Literal["open", "applied", "dismissed"]
    applied_at: AwareDatetime | None = None
    expires_at: AwareDatetime


class RefreshAppliedIn(ApiModel):
    applied_at: AwareDatetime | None = None
    # the description as published; when sent, every suggested line must be visible in it
    applied_text: str | None = Field(default=None, max_length=5000)


class HygieneData(BaseModel):
    eligibility: RefreshEligibility | None = None
    headline: str | None = None
    items: list[RefreshSuggestionView]
    next_cursor: str | None = None
    limit: int


class CreatorView(BaseModel):
    # grouped by relevance, ordered by the one criterion the user picked; raw stats only, no score
    channel: ChannelRef
    group: Literal["strong", "related"]
    position: int
    sort: str
    sort_value: float | str | None
    subscriber_band: str
    uploads_90d: int
    match_share: float
    topics_covered: int
    performance: CreatorPerformance
    consistency: CreatorConsistency
    collaborations: list[CollaborationEvidence] = []
    reference_videos: list[ReferenceVideo] = []
    recommendation_id: str
    summary: str


class CreatorsData(BaseModel):
    sort: str
    available_sorts: list[str]
    items: list[CreatorView]
    next_cursor: str | None = None
    limit: int
