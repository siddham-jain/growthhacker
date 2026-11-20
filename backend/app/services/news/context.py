from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from uuid import UUID
from zoneinfo import ZoneInfo

from app.core.config import NewsEnvelope, NewsSettings
from app.domain.competitors import CandidateStatus, CompetitorCandidate
from app.domain.profile import BusinessProfile, BuyerQuestion, FreshnessProfile, OperatingLevel, ReviewStatus
from app.domain.youtube import VideoRecord

CONFIRMED_COMPETITOR = frozenset(
    {CandidateStatus.confirmed, CandidateStatus.auto_confirmed, CandidateStatus.user_added}
)
LEVEL_ORDER = (OperatingLevel.none, OperatingLevel.low, OperatingLevel.medium, OperatingLevel.high)


def raised(level: OperatingLevel) -> OperatingLevel:
    return LEVEL_ORDER[min(LEVEL_ORDER.index(level) + 1, len(LEVEL_ORDER) - 1)]


@dataclass(frozen=True)
class NewsClientContext:
    """everything the radar knows about one client for one run; loaded by the composition root."""

    workspace_id: UUID
    client_id: UUID
    profile: BusinessProfile
    buyer_questions: Sequence[BuyerQuestion] = ()
    competitors: Sequence[CompetitorCandidate] = ()
    own_channel_id: str | None = None
    own_videos: Sequence[VideoRecord] = ()
    # own video id -> views at day 7 divided by the client's own day-7 baseline (06)
    own_day7_ratios: Mapping[str, float] = field(default_factory=dict)
    own_subscriber_count: int | None = None
    window_days: int = 30

    @property
    def freshness(self) -> FreshnessProfile:
        return self.profile.freshness

    @property
    def level(self) -> OperatingLevel:
        return self.freshness.operating_level

    @property
    def profile_version(self) -> int:
        return self.profile.version

    @property
    def is_health(self) -> bool:
        return self.freshness.domain_kind == "healthcare" or (
            self.freshness.regulated_domain and self.freshness.domain_kind == "none"
        )

    @property
    def regions(self) -> list[str]:
        regions = list(self.freshness.regions)
        country = self.profile.identity.country
        if not regions and country:
            regions = [country]
        return regions

    @property
    def languages(self) -> list[str]:
        return list(self.freshness.languages) or [self.profile.identity.primary_language]

    @property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.profile.identity.timezone or "UTC")

    @property
    def confirmed_competitors(self) -> list[CompetitorCandidate]:
        return [c for c in self.competitors if c.status in CONFIRMED_COMPETITOR]

    @property
    def active_buyer_questions(self) -> list[BuyerQuestion]:
        return [
            q for q in self.buyer_questions if q.status in (ReviewStatus.confirmed, ReviewStatus.proposed)
        ]

    @property
    def brand_terms(self) -> list[str]:
        identity = self.profile.identity
        return [identity.name, *identity.brand_aliases]

    @property
    def own_domains(self) -> list[str]:
        identity = self.profile.identity
        domains = list(identity.website_domains)
        if identity.website is not None:
            domains.append(identity.website.registrable_domain)
        return domains

    def envelope(self, settings: NewsSettings, level: OperatingLevel | None = None) -> NewsEnvelope:
        return settings.envelopes[(level or self.level).value]
