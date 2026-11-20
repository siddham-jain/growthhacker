from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, Field

from app.api.schemas.clients import ChannelRef
from app.api.schemas.common import ApiModel
from app.domain.analytics import (
    ChannelScorecard,
    CollabMention,
    OwnPerformanceReport,
    TraitLift,
    VideoMetrics,
    VideoTraits,
)
from app.domain.recommendations import (
    InsufficiencyNotice,
    Recommendation,
    RecommendationFeedback,
    ReferenceVideo,
    StrictRecommendation,
    WhiteSpaceNote,
)
from app.domain.youtube import OwnAnalytics


class OwnPerformanceData(BaseModel):
    # the three sources stay separate sibling sections by policy; never merged
    youtube_public: OwnPerformanceReport | None
    youtube_analytics: OwnAnalytics | None = None
    website: dict[str, Any] | None = None


class TopicCoverage(BaseModel):
    topic_ref_id: str
    videos: int
    outliers: int


class CompetitorDetail(BaseModel):
    scorecard: ChannelScorecard
    cadence_series: list[dict[str, Any]] = []
    top_outliers: list[VideoMetrics] = Field(default=[], max_length=10)
    trait_lift: list[TraitLift] = []
    topic_coverage: list[TopicCoverage] = []
    collaborations: list[CollabMention] = []


class VideoRow(BaseModel):
    metrics: VideoMetrics
    traits: VideoTraits | None = None


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


class RecommendationsData(BaseModel):
    items: list[RecommendationOut] | list[StrictRecommendation]
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
    youtube_results_7d: int | None
    outliers: int | None


class NewsOpportunityView(BaseModel):
    id: str
    news: NewsRef
    recommendation_id: str | None = None
    relevance: float
    offering_refs: list[str] = []
    demand_check: DemandCheckView
    status: Literal["new", "dismissed", "used"]


class RefreshInsertion(BaseModel):
    anchor: Literal["append", "after_line"]
    line_index: int | None = None
    text: str
    keyword: str | None = None
    reason: str
    evidence_refs: list[str] = []


class RefreshVideo(BaseModel):
    video_id: str
    title: str
    published_at: AwareDatetime
    url: str


class RefreshSuggestionView(BaseModel):
    id: str
    video: RefreshVideo
    warranted_because: list[str]
    original_description_hash: str
    insertions: list[RefreshInsertion]
    preview_diff: str = Field(max_length=20_000)
    status: Literal["open", "applied", "dismissed"]
    applied_at: AwareDatetime | None = None


class RefreshAppliedIn(ApiModel):
    applied_at: AwareDatetime | None = None
    applied_text: str | None = Field(default=None, max_length=5000)


class CreatorCollaboration(BaseModel):
    video_id: str
    competitor_channel_id: str
    label: Literal["mention", "disclosed_paid_promotion"]


class CreatorView(BaseModel):
    channel: ChannelRef
    rank: int
    relevance: str
    recent_performance_vs_niche: str | None = None
    consistency: str | None = None
    collaborations: list[CreatorCollaboration] = []
    reference_videos: list[ReferenceVideo] = []
    recommendation_id: str | None = None
