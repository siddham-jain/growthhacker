from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from app.domain.common import ChannelId, CountryCode, DomainModel, SubscriberBand, UnitInterval, VideoId
from app.domain.youtube import OwnershipVerdict


class CompetitorRelation(StrEnum):
    direct = "direct"
    direct_public = "direct_public"
    aggregator = "aggregator"
    content = "content"
    benchmark = "benchmark"
    self_ = "self"
    subsidiary = "subsidiary"
    parent = "parent"
    partner = "partner"
    supplier = "supplier"
    insurer = "insurer"
    unrelated = "unrelated"


class CandidateRole(StrEnum):
    competitor_set = "competitor_set"
    niche_set = "niche_set"
    benchmark_set = "benchmark_set"
    creator_seed = "creator_seed"
    excluded = "excluded"


class OwnerKind(StrEnum):
    organisation = "organisation"
    individual = "individual"
    unknown = "unknown"


class CandidateStatus(StrEnum):
    suggested = "suggested"
    confirmed = "confirmed"
    auto_confirmed = "auto_confirmed"
    rejected = "rejected"
    unresolved = "unresolved"
    no_channel = "no_channel"
    excluded = "excluded"
    user_added = "user_added"


class CandidateSource(DomainModel):
    kind: Literal["llm", "web_search", "website_hint", "niche_search", "user", "rediscovery_news"]
    ref: str
    snippet: str | None = Field(default=None, max_length=500)
    retrieved_at: AwareDatetime


class ChannelEvidence(DomainModel):
    # copied from ChannelRecord at fetch time; refreshed or deleted within 30 days
    subscriber_count: int | None
    video_count: int
    country: str | None
    published_at: AwareDatetime
    uploads_90d: int
    last_upload_at: AwareDatetime | None
    recent_upload_ids: list[VideoId] = Field(default=[], max_length=50)
    fetched_at: AwareDatetime


class OfferingMatch(DomainModel):
    offering_id: str
    verdict: Literal["yes", "partial"]
    quote: str
    source_url: str


class BusinessRelevance(DomainModel):
    # web-derived only; never combined with YouTube data
    offering_overlap: UnitInterval
    matched_offerings: list[OfferingMatch] = []
    geo_overlap: UnitInterval
    geo_basis: str
    source_agreement: UnitInterval
    tier: Literal["high", "medium", "low"]


class TopicUploadMatch(DomainModel):
    topic_ref_id: str
    upload_matches: int


class YouTubeRelevance(DomainModel):
    # api-derived only; separate raw signals, no composite score
    activity: UnitInterval
    content_overlap: UnitInterval
    matched_topic_refs: list[TopicUploadMatch] = []
    size_band: Literal["peer", "larger", "much_larger", "smaller", "much_smaller", "unknown"]
    subscriber_band: SubscriberBand


class ChannelMatch(DomainModel):
    channel_id: ChannelId
    handle: str | None = None
    title: str
    resolution_method: Literal[
        "site_link",
        "jsonld_sameas",
        "embed_vote",
        "websearch_youtube",
        "for_username",
        "for_handle",
        "search_list",
        "user_url",
        "video_owner",
        "playlist_owner",
        "resolution_cache",
    ]
    variant: Literal["main", "regional", "language", "specialty", "person", "unknown"]
    is_primary: bool
    ownership: OwnershipVerdict
    youtube: ChannelEvidence
    youtube_relevance: YouTubeRelevance
    flags: list[str] = []


class GeoRef(DomainModel):
    city: str | None = None
    region: str | None = None
    country_iso2: CountryCode


class CompetitorCandidate(DomainModel):
    id: UUID
    client_id: UUID
    brand_name: str
    normalized_name: str
    aliases: list[str] = []
    official_domain: str | None = None
    relation: CompetitorRelation
    owner_kind: OwnerKind
    role: CandidateRole
    geography: list[GeoRef] = []
    sources: list[CandidateSource]
    business: BusinessRelevance
    channels: list[ChannelMatch] = []
    confidence: Literal["high", "medium", "low"]
    status: CandidateStatus
    flags: list[str] = []
    evidence_summary: str
    decided_by: Literal["user", "auto", "system"] | None = None
    decided_at: AwareDatetime | None = None
    rejection_reason: str | None = None
    first_seen_run_id: UUID
    last_seen_run_id: UUID

    @property
    def primary_channel_id(self) -> str | None:
        return next((channel.channel_id for channel in self.channels if channel.is_primary), None)

    @model_validator(mode="after")
    def _at_most_one_primary(self) -> "CompetitorCandidate":
        if sum(channel.is_primary for channel in self.channels) > 1:
            raise ValueError("a candidate has at most one primary channel")
        return self


class CompetitorSet(DomainModel):
    # the 3..10 limit applies to competitors only; other list caps are configurable
    client_id: UUID
    version: int = Field(ge=1)
    competitor_channel_ids: list[ChannelId] = Field(min_length=3, max_length=10)
    niche_channel_ids: list[ChannelId] = []
    benchmark_channel_ids: list[ChannelId] = []
    creator_seed_channel_ids: list[ChannelId] = []
    contains_content_competitors: bool = False
    confirmed_by: Literal["user", "auto"]
    confirmed_at: AwareDatetime

    @model_validator(mode="after")
    def _lists_are_disjoint(self) -> "CompetitorSet":
        competitors = set(self.competitor_channel_ids)
        if len(competitors) != len(self.competitor_channel_ids):
            raise ValueError("competitor channel ids must be unique")
        if competitors & (set(self.niche_channel_ids) | set(self.benchmark_channel_ids)):
            raise ValueError("a channel cannot be both a competitor and a niche or benchmark channel")
        return self


class DriftEvent(DomainModel):
    client_id: UUID
    candidate_id: UUID
    channel_id: ChannelId
    kind: Literal[
        "channel_missing",
        "inactive",
        "renamed",
        "ownership_weakened",
        "overlap_drop",
        "niche_riser",
        "new_entrant",
        "profile_changed",
    ]
    detail: dict[str, Any] = {}
    detected_at: AwareDatetime
    acknowledged_at: AwareDatetime | None = None
