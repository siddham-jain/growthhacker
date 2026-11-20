from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.domain.common import ChannelId, DomainModel
from app.domain.competitors import (
    CandidateRole,
    CandidateSource,
    CandidateStatus,
    CompetitorCandidate,
    CompetitorRelation,
    GeoRef,
    OfferingMatch,
    OwnerKind,
)
from app.domain.youtube import ChannelRecord, OwnershipLabel, OwnershipVerdict, VideoRecord
from app.ports.youtube import PlaylistItem
from app.services.competitors.identity.normalize import registrable_domain
from app.services.youtube.ownership import LINK_SIGNALS

ResolutionMethod = Literal[
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
RelationGuess = Literal[
    "direct",
    "direct_public",
    "aggregator",
    "content",
    "benchmark",
    "subsidiary",
    "sister",
    "parent",
    "unknown",
]
DIRECT_RELATIONS = frozenset({CompetitorRelation.direct, CompetitorRelation.direct_public})
WEB_SOURCE_KINDS = frozenset({"web_search", "rediscovery_news"})


class UserCompetitorInput(DomainModel):
    raw: str = Field(min_length=1, max_length=500)
    intended_role: CandidateRole = CandidateRole.competitor_set


class DiscoveryWarning(DomainModel):
    code: str
    message: str = Field(max_length=200)


class DiscoveryUsage(DomainModel):
    yt_units: int = 0
    search_calls: int = 0
    tavily_credits: float = 0.0
    llm_tokens_in: int = 0
    llm_tokens_out: int = 0
    llm_usd: float = 0.0
    seconds: float = 0.0


class LinkEvidence(DomainModel):
    # pre-verification evidence that a channel belongs to the entity, gathered while resolving
    name: Literal[
        "site_links_channel",
        "jsonld_sameas",
        "embed_vote",
        "embed_majority",
        "websearch_youtube_hit",
        "user_asserted",
    ]
    detail: str
    source_url: str | None = None
    rank: int | None = None


class LeadRef(DomainModel):
    kind: Literal["channel_id", "handle", "username", "custom", "video", "playlist"]
    value: str
    method: ResolutionMethod
    evidence: list[LinkEvidence] = []
    guessed: bool = False


class RawCandidate(DomainModel):
    name: str = Field(min_length=1, max_length=200)
    source: CandidateSource
    domain: str | None = None
    # false when the domain came from a model reading untrusted text that never showed it
    domain_grounded: bool = True
    relation_guess: RelationGuess = "unknown"
    aliases: list[str] = []
    geography: list[GeoRef] = []
    channel_id: ChannelId | None = None
    channel_record: ChannelRecord | None = None
    query_hits: int = 0
    total_hits: int = 0
    lead_refs: list[LeadRef] = []
    intended_role: CandidateRole | None = None


@dataclass
class ChannelLead:
    channel_id: str
    method: ResolutionMethod
    evidence: list[LinkEvidence] = field(default_factory=list)
    guessed: bool = False
    record: ChannelRecord | None = None
    uploads: list[VideoRecord] | None = None
    upload_items: list[PlaylistItem] = field(default_factory=list)
    verdict: OwnershipVerdict | None = None
    variant: Literal["main", "regional", "language", "specialty", "person", "unknown"] = "unknown"
    flags: set[str] = field(default_factory=set)

    @property
    def label(self) -> OwnershipLabel | None:
        return self.verdict.label if self.verdict else None

    @property
    def score(self) -> float:
        return self.verdict.score if self.verdict else 0.0

    @property
    def has_link_signal(self) -> bool:
        return bool(self.verdict) and any(signal.name in LINK_SIGNALS for signal in self.verdict.signals)

    def add_evidence(self, items: list[LinkEvidence]) -> None:
        known = {(item.name, item.detail) for item in self.evidence}
        self.evidence.extend(item for item in items if (item.name, item.detail) not in known)


@dataclass
class KnownCandidate:
    id: UUID
    brand_name: str
    normalized_name: str
    official_domain: str | None
    aliases: list[str]
    channel_ids: list[str]
    status: CandidateStatus
    role: CandidateRole
    relation: CompetitorRelation
    decided_by: str | None
    first_seen_run_id: UUID | None
    rejection_reason: str | None = None
    owner_kind: OwnerKind = OwnerKind.unknown


@dataclass
class CandidateEntity:
    key: str
    brand_name: str
    normalized_name: str
    aliases: set[str] = field(default_factory=set)
    official_domain: str | None = None
    relation: CompetitorRelation | None = None
    relation_guesses: list[str] = field(default_factory=list)
    owner_kind: OwnerKind = OwnerKind.unknown
    geography: list[GeoRef] = field(default_factory=list)
    sources: list[CandidateSource] = field(default_factory=list)
    flags: set[str] = field(default_factory=set)
    channel_hints: dict[str, ChannelRecord | None] = field(default_factory=dict)
    lead_refs: list[LeadRef] = field(default_factory=list)
    query_hits: int = 0
    total_hits: int = 0
    intended_role: CandidateRole | None = None
    offering_matches: list[OfferingMatch] = field(default_factory=list)
    status: CandidateStatus | None = None
    rejection_reason: str | None = None
    known: KnownCandidate | None = None
    leads: dict[str, ChannelLead] = field(default_factory=dict)
    resolution_attempted: bool = False

    @property
    def source_kinds(self) -> set[str]:
        return {source.kind for source in self.sources}

    @property
    def independent_sources(self) -> int:
        # each web domain naming the brand counts once and its own site never does; other kinds count once each
        origins: set[str] = set()
        for source in self.sources:
            domain = registrable_domain(source.ref) if source.kind in WEB_SOURCE_KINDS else None
            if domain is None:
                origins.add(source.kind)
            elif domain != self.official_domain:
                origins.add(domain)
        return len(origins)

    @property
    def is_user(self) -> bool:
        return "user" in self.source_kinds

    @property
    def is_excluded(self) -> bool:
        return self.status in {CandidateStatus.excluded, CandidateStatus.rejected}

    @property
    def names(self) -> list[str]:
        return [self.brand_name, *sorted(self.aliases)]

    def best_lead(self) -> ChannelLead | None:
        return max(self.leads.values(), key=lambda lead: lead.score, default=None)

    def is_resolved(self, verified_only: bool = True) -> bool:
        wanted = (
            {OwnershipLabel.verified} if verified_only else {OwnershipLabel.verified, OwnershipLabel.likely}
        )
        return any(lead.label in wanted for lead in self.leads.values())


class CompetitorSetProposal(DomainModel):
    # a proposed set before confirmation; it may hold fewer than three competitors
    client_id: UUID
    competitor_channel_ids: list[ChannelId] = []
    niche_channel_ids: list[ChannelId] = []
    benchmark_channel_ids: list[ChannelId] = []
    creator_seed_channel_ids: list[ChannelId] = []
    contains_content_competitors: bool = False
    fairness_swap: bool = False
    ready: bool = False
    auto_confirmable: bool = False


class DiscoveryResult(DomainModel):
    discovery_run_id: UUID
    candidates: list[CompetitorCandidate]
    proposal: CompetitorSetProposal
    own_channel_suggestions: list[ChannelId] = []
    warnings: list[DiscoveryWarning] = []
    usage: DiscoveryUsage
    partial: bool = False
