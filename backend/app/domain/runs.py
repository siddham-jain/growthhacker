from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field

from app.domain.analytics import ViewsAtAgeSummary
from app.domain.common import ChannelId, DomainModel, VideoId


class RunKind(StrEnum):
    onboarding = "onboarding"
    analysis = "analysis"
    daily_refresh = "daily_refresh"
    cards = "cards"
    news_scan = "news_scan"
    recrawl = "recrawl"
    backtest = "backtest"


class RunTrigger(StrEnum):
    user = "user"
    schedule = "schedule"
    system = "system"
    resume = "resume"
    evaluation = "evaluation"


class RunStatus(StrEnum):
    queued = "queued"
    running = "running"
    cancelling = "cancelling"
    succeeded = "succeeded"
    partial = "partial"
    failed = "failed"
    cancelled = "cancelled"
    superseded = "superseded"


ACTIVE_RUN_STATUSES: frozenset[RunStatus] = frozenset(
    {RunStatus.queued, RunStatus.running, RunStatus.cancelling}
)
TERMINAL_RUN_STATUSES: frozenset[RunStatus] = frozenset(set(RunStatus) - ACTIVE_RUN_STATUSES)


class StageStatus(StrEnum):
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    reused = "reused"
    degraded = "degraded"
    failed = "failed"
    skipped = "skipped"
    cancelled = "cancelled"
    interrupted = "interrupted"


class ClientLifecycle(StrEnum):
    # a client state, not a run status; awaiting_confirmation lives here
    created = "created"
    onboarding = "onboarding"
    onboarding_failed = "onboarding_failed"
    awaiting_confirmation = "awaiting_confirmation"
    analyzing = "analyzing"
    active = "active"
    paused = "paused"
    archived = "archived"
    deleting = "deleting"


class RunEventType(StrEnum):
    run_queued = "run.queued"
    run_started = "run.started"
    run_progress = "run.progress"
    run_awaiting_confirmation = "run.awaiting_confirmation"
    run_succeeded = "run.succeeded"
    run_partial = "run.partial"
    run_failed = "run.failed"
    run_cancelled = "run.cancelled"
    run_superseded = "run.superseded"
    stage_started = "stage.started"
    stage_step = "stage.step"
    stage_progress = "stage.progress"
    stage_retrying = "stage.retrying"
    stage_reused = "stage.reused"
    stage_succeeded = "stage.succeeded"
    stage_degraded = "stage.degraded"
    stage_failed = "stage.failed"
    stage_skipped = "stage.skipped"
    insight = "insight"
    notice = "notice"
    results_published = "results.published"


class RunNoticeCode(StrEnum):
    quota_limited = "quota_limited"
    llm_fallback = "llm_fallback"
    partial_profile = "partial_profile"
    thin_data = "thin_data"
    stale_config = "stale_config"
    competitor_unavailable = "competitor_unavailable"
    news_skipped = "news_skipped"
    oauth_revoked = "oauth_revoked"
    data_stale = "data_stale"


class RunNotice(DomainModel):
    code: RunNoticeCode
    stage: str | None = None
    message: str = Field(max_length=200)
    detail: dict[str, Any] = {}
    suggest: dict[str, Any] | None = None


class Counts(DomainModel):
    done: int = Field(ge=0)
    total: int | None = Field(default=None, ge=0)
    unit: str


class PageReadPayload(DomainModel):
    kind: Literal["page_read"] = "page_read"
    url: str
    path: str
    title: str | None
    page_type: Literal["service", "about", "location", "pricing", "blog", "other"]


class PageSkippedPayload(DomainModel):
    kind: Literal["page_skipped"] = "page_skipped"
    url: str
    reason: Literal["robots", "timeout", "non_html", "duplicate", "budget", "blocked"]


class ProfileFieldPayload(DomainModel):
    kind: Literal["profile_field"] = "profile_field"
    field: Literal["offerings", "value_props", "locations", "conversion_actions", "buyer_questions"]
    count: int
    preview: list[str] = Field(default=[], max_length=5)


class ChannelResolvedPayload(DomainModel):
    kind: Literal["channel_resolved"] = "channel_resolved"
    channel_id: ChannelId
    title: str
    handle: str | None
    thumbnail_url: str | None
    subscriber_count: int | None
    verified_by: Literal["website_link", "channel_link", "oauth", "manual"]


class CompetitorCandidatePayload(DomainModel):
    kind: Literal["competitor_candidate"] = "competitor_candidate"
    candidate_id: UUID
    name: str
    channel_id: ChannelId | None
    thumbnail_url: str | None
    source: Literal["web_search", "youtube_search", "profile", "llm", "user"]
    evidence_count: int


class VideosFetchedPayload(DomainModel):
    kind: Literal["videos_fetched"] = "videos_fetched"
    channel_id: ChannelId
    channel_title: str
    count: int
    window_days: int


class SnapshotProgressPayload(DomainModel):
    kind: Literal["snapshot_progress"] = "snapshot_progress"
    videos_tracked: int
    snapshots_taken: int


class TopicSummaryPayload(DomainModel):
    kind: Literal["topic_summary"] = "topic_summary"
    topic_ref_id: str
    label: str
    matched_videos: int
    review_flagged: int


class OutlierFoundPayload(DomainModel):
    kind: Literal["outlier_found"] = "outlier_found"
    video_id: VideoId
    channel_title: str
    title: str
    outlier_ratio: float
    views_at_age: ViewsAtAgeSummary


class TraitLiftPayload(DomainModel):
    kind: Literal["trait_lift"] = "trait_lift"
    trait: str
    lift: float
    outliers_with_trait: int
    sample: int


class NewsFoundPayload(DomainModel):
    kind: Literal["news_found"] = "news_found"
    news_id: UUID
    headline: str
    source_domain: str
    published_at: AwareDatetime | None


class CardWrittenPayload(DomainModel):
    kind: Literal["card_written"] = "card_written"
    recommendation_id: str
    type: str
    summary: str
    lens: list[str]
    confidence_label: str


class RefreshSuggestedPayload(DomainModel):
    kind: Literal["refresh_suggested"] = "refresh_suggested"
    video_id: VideoId
    title: str
    insertions: int


class QuotaPayload(DomainModel):
    kind: Literal["quota"] = "quota"
    youtube_units_used: int
    search_calls_used: int
    remaining_today: int


class FallbackPayload(DomainModel):
    kind: Literal["fallback"] = "fallback"
    component: Literal["llm", "tavily", "crawler"]
    reason: str
    mode: Literal["template", "secondary_provider", "skipped"]


class SourceFallbackPayload(DomainModel):
    kind: Literal["source_fallback"] = "source_fallback"
    from_source: str
    to_source: str
    reason: str | None = None


EventPayload = Annotated[
    PageReadPayload
    | PageSkippedPayload
    | ProfileFieldPayload
    | ChannelResolvedPayload
    | CompetitorCandidatePayload
    | VideosFetchedPayload
    | SnapshotProgressPayload
    | TopicSummaryPayload
    | OutlierFoundPayload
    | TraitLiftPayload
    | NewsFoundPayload
    | CardWrittenPayload
    | RefreshSuggestedPayload
    | QuotaPayload
    | FallbackPayload
    | SourceFallbackPayload,
    Field(discriminator="kind"),
]


class RunEvent(DomainModel):
    run_id: UUID
    client_id: UUID
    seq: int = Field(ge=1)
    type: RunEventType
    stage: str | None = None
    step: str | None = None
    status: StageStatus | RunStatus | None = None
    level: Literal["info", "warning", "error"] = "info"
    message: str = Field(max_length=200)
    counts: Counts | None = None
    percent: float | None = Field(default=None, ge=0.0, le=100.0)
    stage_percent: float | None = Field(default=None, ge=0.0, le=100.0)
    ts: AwareDatetime
    payload: EventPayload | None = None
