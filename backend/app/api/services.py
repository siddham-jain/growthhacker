"""service seams for routers whose engines live in other components.

each protocol is implemented by its owner and registered with api_service_registry under the
name below; until then the routes answer 501 not_implemented instead of inventing data.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal, Protocol
from uuid import UUID

from app.api.pagination import Page, PageRequest
from app.api.schemas.admin import LabelIn, LabelOut, OpsQuotaOut, OwnerReport, PrecisionOut, ReviewIn
from app.api.schemas.results import (
    CompetitorDetail,
    CompetitorSheet,
    CreatorsData,
    EarlySignalView,
    FeedbackIn,
    GapMatrixData,
    HygieneData,
    NewsOpportunityView,
    OutlierRow,
    OwnPerformanceData,
    RecommendationOut,
    RefreshAppliedIn,
    RefreshSuggestionView,
    TopicSummary,
    TrendingData,
    VideoRow,
)
from app.api.schemas.settings import (
    LLMSettingsIn,
    LLMSettingsOut,
    LLMTestOut,
    NotificationSettingsOut,
    NotificationSettingsPatch,
    ProviderName,
)
from app.api.schemas.setup import (
    BusinessProfileView,
    BuyerQuestionCreate,
    BuyerQuestionItem,
    ChannelResolveOut,
    CompetitorAddIn,
    CompetitorRejectIn,
    CompetitorRolePatch,
    CompetitorView,
    KeywordCreate,
    KeywordItem,
    TopicRefPatch,
)
from app.core.registry import Registry
from app.core.tenancy import TenantScope
from app.domain.analytics import TraitLift
from app.domain.news import NewsSignal
from app.domain.recommendations import InsufficiencyNotice, RecommendationFeedback, WhiteSpaceNote

api_service_registry: Registry[Callable[[Any], Any]] = Registry("api_service_registry")

CHANNELS = "channels"
PROFILE = "profile"
TOPIC_REFS = "topic_refs"
COMPETITORS = "competitors"
ANALYTICS = "analytics"
RECOMMENDATIONS = "recommendations"
NEWS = "news"
REFRESH = "refresh"
CREATORS = "creators"
YOUTUBE_OAUTH = "youtube_oauth"
EVALUATION = "evaluation"
OPS = "ops"
SETTINGS = "settings"
NOTIFICATIONS = "notifications"

TopicRefKind = Literal["keyword", "buyer_question"]
AnalyticsScope = Literal["own", "competitors", "niche"]


class ChannelService(Protocol):
    # 02: url, @handle or UC id -> candidates with ownership evidence (<= 3 api units)
    async def resolve(self, scope: TenantScope, channel_ref: str) -> ChannelResolveOut: ...


class ProfileService(Protocol):
    async def get(self, scope: TenantScope) -> BusinessProfileView: ...

    async def patch(
        self, scope: TenantScope, merge_patch: dict[str, Any], expected_version: int
    ) -> BusinessProfileView: ...


class TopicRefService(Protocol):
    async def list_items(
        self, scope: TenantScope, kind: TopicRefKind, status: str | None, page: PageRequest
    ) -> Page[KeywordItem] | Page[BuyerQuestionItem]: ...

    async def create(
        self, scope: TenantScope, kind: TopicRefKind, body: KeywordCreate | BuyerQuestionCreate
    ) -> KeywordItem | BuyerQuestionItem: ...

    async def update(
        self, scope: TenantScope, kind: TopicRefKind, item_id: str, patch: TopicRefPatch
    ) -> KeywordItem | BuyerQuestionItem: ...

    async def delete(self, scope: TenantScope, kind: TopicRefKind, item_id: str) -> None: ...

    async def bulk_status(
        self, scope: TenantScope, kind: TopicRefKind, item_ids: list[str], status: str
    ) -> int: ...


class CompetitorService(Protocol):
    async def list_competitors(
        self, scope: TenantScope, status: str | None, page: PageRequest
    ) -> Page[CompetitorView]: ...

    async def add(self, scope: TenantScope, body: CompetitorAddIn) -> CompetitorView: ...

    async def confirm(self, scope: TenantScope, competitor_id: str) -> CompetitorView: ...

    async def reject(
        self, scope: TenantScope, competitor_id: str, body: CompetitorRejectIn
    ) -> CompetitorView: ...

    async def set_role(
        self, scope: TenantScope, competitor_id: str, body: CompetitorRolePatch
    ) -> CompetitorView: ...

    async def delete(self, scope: TenantScope, competitor_id: str) -> None: ...


class AnalyticsService(Protocol):
    # every call reads the published run's results; the api wraps them without reshaping numbers
    async def own_performance(
        self, scope: TenantScope, run_id: UUID, window_days: int
    ) -> OwnPerformanceData: ...

    async def competitors(
        self, scope: TenantScope, run_id: UUID, window_days: int, sort: str | None
    ) -> list[CompetitorSheet]: ...

    async def competitor(
        self, scope: TenantScope, run_id: UUID, window_days: int, channel_id: str
    ) -> CompetitorDetail: ...

    async def competitor_videos(
        self,
        scope: TenantScope,
        run_id: UUID,
        window_days: int,
        channel_id: str,
        sort: str,
        topic_ref_id: str | None,
        page: PageRequest,
    ) -> Page[VideoRow]: ...

    async def trending(self, scope: TenantScope, run_id: UUID, window_days: int) -> TrendingData: ...

    async def outliers(
        self,
        scope: TenantScope,
        run_id: UUID,
        window_days: int,
        analytics_scope: AnalyticsScope,
        min_ratio: float | None,
        page: PageRequest,
    ) -> Page[OutlierRow]: ...

    async def patterns(
        self, scope: TenantScope, run_id: UUID, window_days: int, analytics_scope: AnalyticsScope
    ) -> list[TraitLift]: ...

    async def topics(self, scope: TenantScope, run_id: UUID, window_days: int) -> list[TopicSummary]: ...

    # buyer questions x tracked channels, counts only (06 gap matrix)
    async def gaps(self, scope: TenantScope, run_id: UUID, window_days: int) -> GapMatrixData: ...


@dataclass(frozen=True)
class RecommendationFilters:
    type: str | None = None
    lens: str | None = None
    buyer_question_id: str | None = None
    confidence: str | None = None
    status: str | None = None
    assignment_types_only: bool = False


@dataclass(frozen=True)
class RecommendationListing:
    items: list[RecommendationOut]
    next_cursor: str | None = None
    notices: list[InsufficiencyNotice] = field(default_factory=list)
    white_space: list[WhiteSpaceNote] = field(default_factory=list)
    suppressed_counts: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class RecommendationLookup:
    client_id: UUID
    run_id: UUID
    item: RecommendationOut


class RecommendationService(Protocol):
    async def cards_written(self, scope: TenantScope, run_id: UUID, window_days: int) -> bool: ...

    async def list_cards(
        self,
        scope: TenantScope,
        run_id: UUID,
        window_days: int,
        filters: RecommendationFilters,
        page: PageRequest,
        user_id: UUID | None,
    ) -> RecommendationListing: ...

    async def get(
        self, scope: TenantScope, recommendation_id: str, user_id: UUID | None
    ) -> RecommendationLookup: ...

    # keyed by stable_key so feedback carries across runs
    async def put_feedback(
        self, scope: TenantScope, recommendation_id: str, user_id: UUID, body: FeedbackIn
    ) -> RecommendationFeedback: ...


class NewsService(Protocol):
    async def opportunities(
        self, scope: TenantScope, run_id: UUID, status: str | None, page: PageRequest
    ) -> Page[NewsOpportunityView]: ...

    async def signals(self, scope: TenantScope, page: PageRequest) -> Page[NewsSignal]: ...

    async def dismiss(self, scope: TenantScope, opportunity_id: str) -> NewsOpportunityView: ...

    async def early_signals(
        self, scope: TenantScope, run_id: UUID, window_days: int
    ) -> list[EarlySignalView]: ...


class RefreshService(Protocol):
    async def list_suggestions(
        self, scope: TenantScope, run_id: UUID, status: str | None, video_id: str | None, page: PageRequest
    ) -> Page[RefreshSuggestionView]: ...

    async def applied(
        self, scope: TenantScope, suggestion_id: str, body: RefreshAppliedIn
    ) -> RefreshSuggestionView: ...

    async def dismiss(self, scope: TenantScope, suggestion_id: str) -> RefreshSuggestionView: ...

    # rule-based description fixes; the same apply/dismiss commands as suggestions
    async def hygiene(self, scope: TenantScope, run_id: UUID, page: PageRequest) -> HygieneData: ...


class CreatorService(Protocol):
    async def list_creators(
        self, scope: TenantScope, run_id: UUID, window_days: int, sort: str | None, page: PageRequest
    ) -> CreatorsData: ...


@dataclass(frozen=True)
class OAuthOutcome:
    status: Literal["connected", "channel_mismatch", "no_channel", "invalid_state", "failed"]
    message: str | None = None


class YouTubeOAuthService(Protocol):
    # 02 owns google, pkce state, tokens and the ownership check; the api maps results to redirects
    async def start(self, scope: TenantScope, user_id: UUID) -> str: ...

    async def complete(self, state: str, code: str, *, user_id: UUID) -> OAuthOutcome: ...

    async def disconnect(self, scope: TenantScope) -> bool: ...


class EvaluationService(Protocol):
    async def backtest_report(self, scope: TenantScope, run_id: UUID) -> OwnerReport: ...

    async def labels(
        self, scope: TenantScope, client_id: UUID | None, page: PageRequest
    ) -> Page[LabelOut]: ...

    async def put_label(self, scope: TenantScope, video_id: str, body: LabelIn) -> LabelOut: ...

    async def precision(self, scope: TenantScope, client_id: UUID | None) -> PrecisionOut: ...

    async def review_queue(self, scope: TenantScope) -> OwnerReport: ...

    async def submit_review(self, scope: TenantScope, body: ReviewIn) -> OwnerReport: ...

    async def review_summary(self, scope: TenantScope) -> OwnerReport: ...


class OpsService(Protocol):
    async def quota(self, scope: TenantScope, day: date | None) -> OpsQuotaOut: ...

    async def report(
        self, scope: TenantScope, start: datetime | None, end: datetime | None
    ) -> OwnerReport: ...


class SettingsService(Protocol):
    # workspace llm provider config; api keys are write-only
    async def llm(self, scope: TenantScope) -> LLMSettingsOut: ...

    async def put_llm(self, scope: TenantScope, body: LLMSettingsIn) -> LLMSettingsOut: ...

    async def test(self, scope: TenantScope, provider: ProviderName | None) -> LLMTestOut: ...


class NotificationService(Protocol):
    # per-client delivery choices, stored ahead of an email sender
    async def get(self, scope: TenantScope) -> NotificationSettingsOut: ...

    async def patch(self, scope: TenantScope, body: NotificationSettingsPatch) -> NotificationSettingsOut: ...
