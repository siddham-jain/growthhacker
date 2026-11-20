from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from pydantic import AwareDatetime, Field

from app.domain.analytics import GapMatrix, OwnPerformanceReport, TraitLift, VideoMetrics, VideoTraits
from app.domain.common import ChannelId, ChannelRole, DomainModel, UnitInterval, WindowDays
from app.domain.competitors import CompetitorCandidate, CompetitorSet
from app.domain.profile import BusinessProfile, BuyerQuestion
from app.domain.recommendations import RecommendationDraft
from app.domain.refresh import DescriptionRefresh
from app.domain.topics import AnswerDirectness, TopicMatch, TopicRef
from app.domain.youtube import ChannelRecord, VideoRecord

LensMode = Literal["both", "views", "conversion"]


class ClientFacts(DomainModel):
    # the slice of the business profile the recommender reads; never blended with youtube numbers
    name: str
    offerings: list[str] = []
    value_propositions: list[str] = []
    locations: list[str] = []
    website_domains: list[str] = []
    brand_aliases: list[str] = []
    niche_keywords: list[str] = []
    vertical: str = ""
    domain_kind: Literal["healthcare", "finance", "legal", "none"] = "none"
    language: str = "en"

    @classmethod
    def from_profile(cls, profile: BusinessProfile) -> "ClientFacts":
        identity = profile.identity
        locations = [*profile.locations.cities, *(item.name for item in profile.locations.items)]
        return cls(
            name=identity.name,
            offerings=[offering.name for offering in profile.offerings],
            value_propositions=[value.statement for value in profile.value_propositions],
            locations=list(dict.fromkeys(locations)),
            website_domains=identity.website_domains,
            brand_aliases=identity.brand_aliases,
            niche_keywords=[keyword.term for keyword in profile.niche_keywords],
            vertical=identity.niche,
            domain_kind=profile.freshness.domain_kind,
            language=identity.primary_language,
        )


class WindowAnalytics(DomainModel):
    # 06 output for one window; metrics cover every eligible video, not only outliers
    window_days: WindowDays
    metrics: dict[str, VideoMetrics]
    traits: dict[str, VideoTraits] = {}
    lifts: list[TraitLift] = []
    gap_matrix: GapMatrix | None = None
    own_report: OwnPerformanceReport | None = None


class TopicSimilarity(DomainModel):
    topic_a: str
    topic_b: str
    score: UnitInterval


class CreatorExclusion(DomainModel):
    channel_id: ChannelId
    until: AwareDatetime


class FeedbackPenalty(DomainModel):
    group_key: str
    factor: float = Field(gt=0.0)
    until: AwareDatetime


class ClientRecommendConfig(DomainModel):
    lens_mode: LensMode = "both"
    overrides: dict = {}
    excluded_formats: list[str] = []
    excluded_creators: list[CreatorExclusion] = []
    feedback_penalties: list[FeedbackPenalty] = []
    disabled_types: list[str] = []
    creator_sort: (
        Literal[
            "matching_uploads", "median_outlier_ratio", "median_views_at_age", "last_upload_at", "uploads_90d"
        ]
        | None
    ) = None


class FeedbackState(DomainModel):
    # stable keys the user dismissed, snoozed or made; they stay hidden across daily runs
    hidden_keys: dict[str, str] = {}


class PreviousCard(DomainModel):
    stable_key: str
    first_seen_at: AwareDatetime
    unchanged_since: AwareDatetime
    evidence_hash: str
    acted: bool = False


class RecommendInputs(DomainModel):
    client_id: UUID
    as_of: AwareDatetime
    selected_window: WindowDays = 30
    client: ClientFacts
    buyer_questions: list[BuyerQuestion] = []
    topic_refs: list[TopicRef] = []
    topic_matches: list[TopicMatch] = []
    directness: list[AnswerDirectness] = []
    topic_similarity: list[TopicSimilarity] = []
    own_channel_id: ChannelId | None = None
    competitor_set: CompetitorSet | None = None
    competitor_candidates: list[CompetitorCandidate] = []
    channels: list[ChannelRecord] = []
    channel_roles: dict[str, ChannelRole] = {}
    videos: list[VideoRecord] = []
    niche_search_hits: dict[str, int] = {}
    analytics: list[WindowAnalytics] = []
    news_drafts: list[RecommendationDraft] = []
    refresh_drafts: list[RecommendationDraft] = []
    refreshes: list[DescriptionRefresh] = []
    config: ClientRecommendConfig = ClientRecommendConfig()
    feedback: FeedbackState = FeedbackState()
    previous_cards: dict[str, PreviousCard] = {}
    data_refreshed_at: AwareDatetime | None = None

    def window(self, window_days: int) -> WindowAnalytics | None:
        return next((item for item in self.analytics if item.window_days == window_days), None)


class RecommendInputsProvider(Protocol):
    # adapter over upstream artifacts and repositories; wired by the composition root
    async def load(self, client_id: UUID, as_of: datetime, window_days: int) -> RecommendInputs: ...
