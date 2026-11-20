from datetime import date
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field

from app.domain.analytics import ViewsAtAgeSummary
from app.domain.common import ChannelId, DomainModel, UnitInterval, VideoId


class EventType(StrEnum):
    product_launch = "product_launch"
    model_release = "model_release"
    regulatory_approval = "regulatory_approval"
    regulatory_action = "regulatory_action"
    clinical_guideline = "clinical_guideline"
    research_publication = "research_publication"
    public_health_advisory = "public_health_advisory"
    outbreak = "outbreak"
    policy_change = "policy_change"
    pricing_change = "pricing_change"
    funding_or_ipo = "funding_or_ipo"
    partnership = "partnership"
    incident = "incident"
    recall = "recall"
    other = "other"


class ConfirmationStatus(StrEnum):
    confirmed = "confirmed"
    officially_announced_upcoming = "officially_announced_upcoming"
    reported = "reported"
    rumour_or_leak = "rumour_or_leak"
    speculative = "speculative"


class SourceTier(StrEnum):
    tier1_primary = "tier1_primary"
    tier1_authority = "tier1_authority"
    tier2_established = "tier2_established"
    tier3_low = "tier3_low"
    blocked = "blocked"


class SafetyFlag(StrEnum):
    tragedy = "tragedy"
    death = "death"
    medical_claim = "medical_claim"
    unverified_health_claim = "unverified_health_claim"
    political = "political"
    legal = "legal"
    controversy = "controversy"
    minors = "minors"
    self_harm = "self_harm"
    fear_inducing = "fear_inducing"


class RelevanceBand(StrEnum):
    none = "none"
    tangential = "tangential"
    adjacent = "adjacent"
    strong = "strong"
    direct = "direct"


class NewsItem(DomainModel):
    id: UUID
    source_key: str
    url: str
    canonical_url: str
    url_hash: str
    domain: str
    title: str
    snippet: str = Field(max_length=1_500)
    language: str | None
    published_at: AwareDatetime | None
    published_precision: Literal["datetime", "date", "none"]
    fetched_at: AwareDatetime
    source_rank_score: float | None = None
    external_ids: dict[str, str] = {}
    source_tier: SourceTier
    flags: list[str] = []
    story_id: UUID | None = None


class NewsStory(DomainModel):
    id: UUID
    first_published_at: AwareDatetime
    last_published_at: AwareDatetime
    item_ids: list[UUID]
    outlet_count: int
    best_tier: SourceTier
    representative_item_id: UUID
    version_tokens: list[str] = []
    status: Literal["developing", "stable", "stale"]


class GroundedEntity(DomainModel):
    text: str
    canonical: str
    kind: Literal[
        "model", "product", "org", "drug", "device", "condition", "policy", "regulator", "person", "other"
    ]
    evidence_item_id: UUID
    evidence_quote: str = Field(max_length=200)


class OfferingLink(DomainModel):
    offering_id: str
    value_prop_ids: list[str] = []
    rationale: str = Field(max_length=240)


class NewsJudgement(DomainModel):
    relevance_band: RelevanceBand
    key_entity: GroundedEntity
    other_entities: list[GroundedEntity] = []
    event_type: EventType
    event_date: date | None
    confirmation: ConfirmationStatus
    offering_links: list[OfferingLink] = []
    buyer_question_ids: list[UUID] = []
    angle: str = Field(max_length=200)
    region_relevance: Literal["primary_region", "global", "other_region_only", "unclear"]
    about_client: bool
    about_competitor_ids: list[UUID] = []
    safety_flags: list[SafetyFlag] = []
    conflicting_reports: bool = False
    conversion_action_id: str | None = None


class CredibilityAssessment(DomainModel):
    best_tier: SourceTier
    independent_outlets: int
    has_primary_source: bool
    has_authoritative_health_source: bool
    peer_reviewed: bool | None = None
    score: UnitInterval
    reasons: list[str] = []


class EarlyVideo(DomainModel):
    video_id: VideoId
    channel_id: ChannelId
    channel_title: str
    published_at: AwareDatetime
    age_hours: float
    views: int
    views_per_hour: float
    views_at_age: ViewsAtAgeSummary
    outlier_ratio: float | None
    is_outlier: bool
    duration_band: str


class DemandCheck(DomainModel):
    status: Literal["checked", "unchecked_quota", "unchecked_disabled", "unchecked_error", "local_only"]
    entity_query: str
    published_after: AwareDatetime
    checked_at: AwareDatetime
    early_videos: list[EarlyVideo] = []
    early_video_count: int
    channel_count: int
    outlier_count: int
    median_views_per_hour: float | None
    saturation: Literal["empty", "emerging", "active", "crowded"]
    quota_units_spent: int


class SafetyDecision(DomainModel):
    decision: Literal["allow", "allow_with_guidance", "needs_review", "block"]
    reasons: list[str] = []
    disclaimers: list[str] = []


class NewsSourceRef(DomainModel):
    url: str
    publisher: str
    published_at: AwareDatetime | None
    tier: SourceTier | None = None
    title: str | None = None


class NewsSignal(DomainModel):
    # ordering is lexicographic (relevance, credibility, freshness, saturation); no blended priority
    id: UUID
    client_id: UUID
    story_id: UUID
    profile_version: int
    judgement: NewsJudgement
    credibility: CredibilityAssessment
    demand: DemandCheck
    aliases: list[str] = []
    is_competitor_entity: bool
    topic_ref_ids: list[str] = []
    first_seen_at: AwareDatetime
    sources: list[NewsSourceRef]
    verification_status: Literal["active", "disputed", "retracted", "superseded"]
    superseded_by: UUID | None = None
    relevance_score: UnitInterval
    urgency: Literal["now", "this_week", "evergreen_window"]
    half_life_hours: int
    expires_at: AwareDatetime
    refresh_keywords: list[str] = []
    refresh_eligible: bool
    safety: SafetyDecision
    status: Literal["new", "drafted", "shown", "dismissed", "used", "suppressed", "expired"]
    suppression_reason: str | None = None
    created_at: AwareDatetime

    @property
    def entity(self) -> str:
        return self.judgement.key_entity.canonical

    @property
    def entity_kind(self) -> str:
        return self.judgement.key_entity.kind

    @property
    def event_date(self) -> date | None:
        return self.judgement.event_date

    @property
    def demand_video_ids(self) -> list[str]:
        return [video.video_id for video in self.demand.early_videos]
