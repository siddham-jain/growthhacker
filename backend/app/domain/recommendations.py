import base64
import hashlib
from datetime import date
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, StringConstraints, model_validator

from app.domain.analytics import TRUE_AGE_METHODS, LiftScope, ViewsAtAgeSummary
from app.domain.common import (
    ChannelId,
    ChannelRole,
    DomainModel,
    FormatSegment,
    SubscriberBand,
    UnitInterval,
    VideoId,
    WindowDays,
)
from app.domain.confidence import Confidence, ConfidenceLabel
from app.domain.news import NewsSourceRef, SafetyFlag
from app.domain.refresh import Advisory, InsertionOp, validate_operations

CONTRACT_VERSION = "1.1"
RECOMMENDATION_ID_PATTERN = r"^rec_[a-z2-7]{12}$"
VIEWS_DAY7_AGE = 7
MAX_REFERENCE_POOL = 12
MAX_DISPLAYED_REFERENCES = 5
SUMMARY_MAX_CHARS = 140
WHY_IT_WORKS_MAX_ITEMS = 3
WHY_IT_WORKS_ITEM_MAX_CHARS = 160
FIT_TO_CLIENT_MAX_CHARS = 200
DATA_DISCLOSURE = "Calculated by GrowthHacker from public YouTube data, not a YouTube metric"

RecommendationId = Annotated[str, StringConstraints(pattern=RECOMMENDATION_ID_PATTERN)]
ReasonCode = Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]*$")]
WhyItWorksItem = Annotated[str, StringConstraints(min_length=1, max_length=WHY_IT_WORKS_ITEM_MAX_CHARS)]


class RecType(StrEnum):
    video_idea = "video_idea"
    title_pattern = "title_pattern"
    thumbnail_pattern = "thumbnail_pattern"
    format = "format"
    creator = "creator"
    news_opportunity = "news_opportunity"
    description_refresh = "description_refresh"


ASSIGNMENT_REC_TYPES: frozenset[RecType] = frozenset(
    {RecType.video_idea, RecType.title_pattern, RecType.thumbnail_pattern, RecType.format, RecType.creator}
)


class Lens(StrEnum):
    views = "views"
    conversion = "conversion"


class EvidenceSource(StrEnum):
    youtube_public = "youtube_public"
    youtube_own_analytics = "youtube_own_analytics"
    spotlit_ai_tracking = "spotlit_ai_tracking"
    website = "website"
    news = "news"
    client_config = "client_config"


def make_stable_key(client_id: UUID, rec_type: RecType, subject_key: str, window_days: int) -> str:
    raw = f"{client_id}|{rec_type.value}|{subject_key}|{window_days}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def recommendation_id(stable_key: str) -> str:
    digest = hashlib.sha1(stable_key.encode("utf-8")).digest()
    return "rec_" + base64.b32encode(digest).decode("ascii").lower()[:12]


class EvidenceNumber(DomainModel):
    value: float
    unit: Literal[
        "count",
        "views",
        "ratio_x",
        "percent",
        "days",
        "minutes",
        "seconds",
        "videos",
        "channels",
        "year",
        "currency",
    ]
    display: str


class Reason(DomainModel):
    # structured evidence; the writer phrases it and the template fallback renders statement verbatim
    reason_id: str
    code: ReasonCode
    params: dict[str, int | float | str] = {}
    statement: str
    numbers: list[EvidenceNumber] = []
    ref_video_ids: list[VideoId] = []
    source: EvidenceSource


class LensAssignment(DomainModel):
    lens: Lens
    reasons: list[Reason]


class GapEvidence(DomainModel):
    topic_id: str
    gap_type: Literal["uncovered", "stale", "underperforming", "none"]
    competitors_covering: int
    competitors_total: int
    niche_outliers_matching: int
    window_outliers_total: int
    client_videos_matching: int
    client_last_covered_at: date | None = None
    client_best_ratio: float | None = None
    gap_size: UnitInterval


class VideoIdeaSubject(DomainModel):
    kind: Literal["video_idea"] = "video_idea"
    topic_id: str
    topic_text: str
    format_key: str | None = None
    length_band: str | None = None
    title_hint_traits: dict[str, str] = {}
    thumbnail_hint_traits: dict[str, str] = {}
    placeholders: list[str] = []


class PatternSpec(DomainModel):
    kind: Literal["title", "thumbnail", "format"]
    traits: dict[str, str]
    scope: Literal[LiftScope.competitor_set, LiftScope.niche]
    lift: float
    ci_low: float
    ci_high: float
    n_with: int
    n_outliers_with: int
    n_total: int
    n_outliers_total: int
    client_usage_share: UnitInterval | None = None
    client_ratio_with: float | None = None
    client_n_with: int = 0


class CollaborationEvidence(DomainModel):
    kind: Literal[
        "mentioned_by_competitor", "mentions_competitor", "sponsor_language", "disclosed_paid_promotion"
    ]
    competitor_channel_id: ChannelId
    competitor_name: str
    video_id: VideoId
    published_at: AwareDatetime
    matched_on: Literal["handle", "channel_url", "custom_url", "website_domain", "brand_name"]
    matched_token: str


class CreatorRelevance(DomainModel):
    match_share: UnitInterval
    topics_covered: int


class CreatorPerformance(DomainModel):
    median_outlier_ratio: float | None
    median_views_at_age: int | None
    niche_median_views_at_age: int | None
    comparison_age_days: int


class CreatorConsistency(DomainModel):
    median_gap_days: float | None
    gap_cv: float | None
    hit_rate: UnitInterval | None
    last_upload_at: date | None


class CreatorSubject(DomainModel):
    # separate simple stats only; there is no composite creator score
    kind: Literal["creator"] = "creator"
    channel_id: ChannelId
    title: str
    handle: str | None = None
    url: str
    subscriber_band: SubscriberBand
    declared_country: str | None = None
    uploads_90d: int
    uploads_checked: int
    relevance: CreatorRelevance
    performance: CreatorPerformance
    consistency: CreatorConsistency
    collaborations: list[CollaborationEvidence] = []


class FormatSuggestion(DomainModel):
    format: Literal["short", "long_explainer", "tutorial", "live_or_premiere"]
    rationale_codes: list[ReasonCode] = []


class NewsCompliance(DomainModel):
    disclaimers: list[str] = []
    review_required: bool
    safety_flags: list[SafetyFlag] = []


class NewsSubject(DomainModel):
    kind: Literal["news"] = "news"
    news_signal_id: UUID
    story_id: UUID
    key_entity: str
    angle: str
    urgency: Literal["now", "this_week", "evergreen_window"]
    expires_at: AwareDatetime
    reference_sources: list[NewsSourceRef] = Field(min_length=1)
    format_suggestion: FormatSuggestion | None = None
    conversion_action_id: str | None = None
    compliance: NewsCompliance


class RefreshSubject(DomainModel):
    kind: Literal["refresh"] = "refresh"
    video_id: VideoId
    operations: list[InsertionOp]
    advisories: list[Advisory] = []
    review_required: bool

    @model_validator(mode="after")
    def _within_edit_limits(self) -> "RefreshSubject":
        validate_operations(self.operations)
        return self


Subject = Annotated[
    VideoIdeaSubject | PatternSpec | CreatorSubject | NewsSubject | RefreshSubject,
    Field(discriminator="kind"),
]

SUBJECT_KIND_BY_TYPE: dict[RecType, str] = {
    RecType.video_idea: "video_idea",
    RecType.title_pattern: "title",
    RecType.thumbnail_pattern: "thumbnail",
    RecType.format: "format",
    RecType.creator: "creator",
    RecType.news_opportunity: "news",
    RecType.description_refresh: "refresh",
}


class ExpectedRange(DomainModel):
    # multiples of the client's own baseline at the comparison age; indicative, never a promise
    low: float | None
    high: float | None
    basis: Literal["peer", "blended", "own", "peer_only_no_baseline", "insufficient", "not_applicable"]
    n_peer: int = 0
    n_peer_effective: float = 0.0
    n_own: int = 0
    comparison_age_days: int
    size_note: Literal["peers_much_smaller", "peers_much_larger"] | None = None
    views_at_age_range: tuple[int, int] | None = None
    display: str

    @model_validator(mode="after")
    def _ordered_bounds(self) -> "ExpectedRange":
        if self.low is not None and self.high is not None and self.low > self.high:
            raise ValueError("expected range low must not exceed high")
        return self


class ScoreBreakdown(DomainModel):
    # internal card ordering only; never exposed and never computed for channels
    components: dict[str, float]
    weights: dict[str, float]
    penalties: dict[str, float]
    total: float


class MatchSpec(DomainModel):
    spec_id: str
    rec_type: RecType
    topic_ids: list[str] = []
    format: FormatSegment | None = None
    length_band: str | None = None
    title_traits: dict[str, bool | str] = {}
    thumbnail_traits: dict[str, bool | str] = {}
    channel_id: ChannelId | None = None
    lens: list[Lens]
    buyer_question_id: UUID | None = None
    specificity: int = Field(ge=0)
    source: Literal["system", "copy_top", "random"]
    source_ref: str | None = None


class GateAction(StrEnum):
    pass_ = "pass"
    repair = "repair"
    downgrade = "downgrade"
    suppress = "suppress"


class GateResult(DomainModel):
    gate: str
    action: GateAction
    reason_code: str | None = None
    features: dict[str, float | int | str | bool] = {}


class ReferenceVideo(DomainModel):
    video_id: VideoId
    url: str
    title: str
    channel: str
    channel_id: ChannelId
    channel_role: ChannelRole
    published_at: AwareDatetime
    views_day7: int | None
    views_at_age: ViewsAtAgeSummary
    outlier_ratio: float | None
    channel_baseline_views_at_age: float | None = None
    is_outlier: bool
    format_key: str | None = None
    length_band: str | None = None
    matched_topic_ids: list[str] = []
    thumbnail_url: str | None = None
    has_paid_product_placement: bool | None = None

    @model_validator(mode="after")
    def _views_day7_is_true_day7(self) -> "ReferenceVideo":
        if self.views_day7 is None:
            return self
        age = self.views_at_age
        if age.age_days != VIEWS_DAY7_AGE or age.method not in TRUE_AGE_METHODS:
            raise ValueError("views_day7 is set only for observed, interpolated or owner day-7 values")
        if age.views != self.views_day7:
            raise ValueError("views_day7 must equal views_at_age.views")
        return self


class RecommendationDraft(DomainModel):
    draft_id: str
    stable_key: str
    client_id: UUID
    type: RecType
    window_days: WindowDays
    as_of: AwareDatetime
    produced_by: str
    subject: Subject
    topic_ids: list[str] = []
    buyer_question_id: UUID | None = None
    related_buyer_question_ids: list[UUID] = []
    reference_pool: list[ReferenceVideo] = Field(default=[], max_length=MAX_REFERENCE_POOL)
    references: list[ReferenceVideo] = Field(default=[], max_length=MAX_DISPLAYED_REFERENCES)
    reasons: list[Reason] = []
    fit_reasons: list[Reason] = []
    lenses: list[LensAssignment] = []
    gap: GapEvidence | None = None
    expected_range: ExpectedRange | None = None
    confidence: Confidence | None = None
    score: ScoreBreakdown | None = None
    match_spec: MatchSpec | None = None
    forbidden_titles: list[str] = []
    allowed_dates: list[date] = []
    entities: list[str] = []
    gate_results: list[GateResult] = []
    critic_flags: list[str] = []
    flags: list[str] = []

    @model_validator(mode="after")
    def _subject_matches_type(self) -> "RecommendationDraft":
        if self.subject.kind != SUBJECT_KIND_BY_TYPE[self.type]:
            raise ValueError(f"{self.type.value} drafts need a {SUBJECT_KIND_BY_TYPE[self.type]} subject")
        return self


class ConversionActionRef(DomainModel):
    id: str
    label: str
    url: str


class NewsExtras(DomainModel):
    reference_sources: list[NewsSourceRef]
    urgency: Literal["now", "this_week", "evergreen_window"]
    format_suggestion: FormatSuggestion | None = None
    conversion_action: ConversionActionRef | None = None
    compliance: NewsCompliance


class RefreshExtras(DomainModel):
    operations: list[InsertionOp]
    advisories: list[Advisory] = []
    review_required: bool
    inserted_bytes: int = Field(ge=0)
    resulting_bytes: int = Field(ge=0)


class RelatedRecommendation(DomainModel):
    type: RecType
    id: RecommendationId


class RecommendationExtras(DomainModel):
    contract_version: Literal["1.1"] = CONTRACT_VERSION
    stable_key: str
    run_id: UUID
    rendered_by: Literal["llm", "template"]
    status: Literal["new", "ongoing", "updated"] = "new"
    first_seen_at: AwareDatetime
    expires_at: AwareDatetime
    buyer_question_id: UUID | None = None
    lens_reasons: list[LensAssignment] = []
    why_it_works_structured: list[Reason] = []
    fit_structured: list[Reason] = []
    expected_range: ExpectedRange | None = None
    gap: GapEvidence | None = None
    subject: Subject | None = None
    related_buyer_questions: list[str] = []
    example_titles: list[str] = []
    related: list[RelatedRecommendation] = []
    flags: list[str] = []
    news: NewsExtras | None = None
    refresh: RefreshExtras | None = None
    data_disclosure: str = DATA_DISCLOSURE


class StrictReferenceVideo(DomainModel):
    video_id: VideoId
    channel: str
    published_at: date
    views_day7: int | None
    outlier_ratio: float | None


class StrictConfidence(DomainModel):
    label: ConfidenceLabel
    outliers: int
    channels: int


class StrictRecommendation(DomainModel):
    # exactly the assignment's output contract, nothing more
    id: RecommendationId
    type: RecType
    summary: str
    lens: list[Lens]
    buyer_question: str | None
    why_it_works: list[str]
    reference_videos: list[StrictReferenceVideo]
    fit_to_client: str
    expected_range_vs_baseline: str | None
    confidence: StrictConfidence
    window_days: WindowDays
    generated_at: AwareDatetime


class Recommendation(DomainModel):
    id: RecommendationId
    type: RecType
    summary: str = Field(min_length=1, max_length=SUMMARY_MAX_CHARS)
    lens: list[Lens] = Field(min_length=1, max_length=2)
    buyer_question: str | None
    why_it_works: list[WhyItWorksItem] = Field(min_length=1, max_length=WHY_IT_WORKS_MAX_ITEMS)
    reference_videos: list[ReferenceVideo]
    fit_to_client: str = Field(min_length=1, max_length=FIT_TO_CLIENT_MAX_CHARS)
    expected_range_vs_baseline: str | None
    confidence: Confidence
    window_days: WindowDays
    generated_at: AwareDatetime
    extras: RecommendationExtras

    @model_validator(mode="after")
    def _consistent(self) -> "Recommendation":
        if len(set(self.lens)) != len(self.lens):
            raise ValueError("lens values must be unique")
        if self.id != recommendation_id(self.extras.stable_key):
            raise ValueError("id must derive from extras.stable_key")
        if (self.extras.news is not None) != (self.type is RecType.news_opportunity):
            raise ValueError("extras.news is present exactly for news_opportunity cards")
        if (self.extras.refresh is not None) != (self.type is RecType.description_refresh):
            raise ValueError("extras.refresh is present exactly for description_refresh cards")
        subject = self.extras.subject
        if subject is not None and subject.kind != SUBJECT_KIND_BY_TYPE[self.type]:
            raise ValueError(f"{self.type.value} cards need a {SUBJECT_KIND_BY_TYPE[self.type]} subject")
        return self

    def strict_contract(self) -> StrictRecommendation:
        return StrictRecommendation(
            id=self.id,
            type=self.type,
            summary=self.summary,
            lens=list(self.lens),
            buyer_question=self.buyer_question,
            why_it_works=list(self.why_it_works),
            reference_videos=[
                StrictReferenceVideo(
                    video_id=ref.video_id,
                    channel=ref.channel,
                    published_at=ref.published_at.date(),
                    views_day7=ref.views_day7,
                    outlier_ratio=ref.outlier_ratio,
                )
                for ref in self.reference_videos
            ],
            fit_to_client=self.fit_to_client,
            expected_range_vs_baseline=self.expected_range_vs_baseline,
            confidence=StrictConfidence(
                label=self.confidence.label,
                outliers=self.confidence.outliers,
                channels=self.confidence.channels,
            ),
            window_days=self.window_days,
            generated_at=self.generated_at,
        )


class InsufficiencyCounts(DomainModel):
    outliers: int = 0
    channels: int = 0
    videos: int = 0
    cards: int = 0


class InsufficiencySuggestion(DomainModel):
    action: Literal[
        "widen_window",
        "add_competitors",
        "add_keywords",
        "add_buyer_questions",
        "connect_oauth",
        "wait",
    ]
    widen_to: WindowDays | None = None
    projected: InsufficiencyCounts | None = None

    @model_validator(mode="after")
    def _widen_needs_target(self) -> "InsufficiencySuggestion":
        if (self.action == "widen_window") != (self.widen_to is not None):
            raise ValueError("widen_to is set exactly when the suggestion is to widen the window")
        return self


class InsufficiencyNotice(DomainModel):
    # thin data says so; rendered by template, never by the writer
    kind: Literal[
        "thin_window",
        "type_thin",
        "no_buyer_questions",
        "no_client_baseline",
        "few_competitors",
        "stale_data",
    ]
    window_days: WindowDays
    rec_type: RecType | None = None
    found: InsufficiencyCounts
    required: InsufficiencyCounts
    suggestion: InsufficiencySuggestion
    reason_code: ReasonCode


class WhiteSpaceNote(DomainModel):
    buyer_question_id: UUID
    text: str
    window_days: WindowDays
    videos_checked: int
    channels_checked: int
    widest_window_checked: WindowDays
    ai_answer_citations: int | None = None


class RecommendationSet(DomainModel):
    client_id: UUID
    window_days: WindowDays
    as_of: AwareDatetime
    generated_at: AwareDatetime
    contract_version: Literal["1.1"] = CONTRACT_VERSION
    recommendations: list[Recommendation]
    notices: list[InsufficiencyNotice] = []
    white_space: list[WhiteSpaceNote] = []
    suppressed_counts: dict[str, int] = {}
    candidate_count: int = 0


class SuppressedRecommendation(DomainModel):
    draft: RecommendationDraft
    phase: Literal["pre_writer", "post_writer", "set_level"]
    gate: str
    reason_code: str
    details: dict[str, float | int | str | bool] = {}
    created_at: AwareDatetime


class FeedbackStatus(StrEnum):
    saved = "saved"
    dismissed = "dismissed"
    done = "done"
    snoozed = "snoozed"


class RecommendationFeedback(DomainModel):
    id: UUID
    client_id: UUID
    recommendation_id: RecommendationId
    stable_key: str
    user_id: UUID
    status: FeedbackStatus | None
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
    linked_video_id: VideoId | None = None
    note: str | None = Field(default=None, max_length=1000)
    created_at: AwareDatetime
