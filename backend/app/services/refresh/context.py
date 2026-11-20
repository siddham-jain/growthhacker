from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from app.core.config import RefreshSettings
from app.domain.news import NewsSignal
from app.domain.profile import BusinessProfile, ConversionAction
from app.domain.topics import TopicMatch, TopicRef
from app.services.refresh.demand import DemandEvidenceCollector
from app.services.refresh.eligibility import EligibilityDecision, VideoState
from app.services.refresh.inputs import RefreshClientOptions
from app.services.refresh.links.status import LinkStatus
from app.services.refresh.links.urls import normalise_url, on_domains
from app.services.refresh.policy.domain import DomainPolicy

CTA_TYPES = frozenset({"book_appointment", "signup", "free_trial", "demo", "contact_form", "quote_request"})
CONTACT_TYPES = frozenset({"call", "whatsapp"})


@dataclass(frozen=True, slots=True)
class RefreshContext:
    """frozen snapshot every detector reads; detectors never touch repositories or the clock."""

    now: datetime
    settings: RefreshSettings
    profile: BusinessProfile
    decision: EligibilityDecision
    policy: DomainPolicy
    options: RefreshClientOptions
    oauth_connected: bool
    topic_refs: Mapping[str, TopicRef]
    matches_by_video: Mapping[str, Sequence[TopicMatch]]
    news_signals: Sequence[NewsSignal]
    demand: DemandEvidenceCollector
    link_statuses: Mapping[str, LinkStatus]
    client_domains: frozenset[str]
    owner_search_terms: Mapping[str, Sequence[str]]
    states: Mapping[str, VideoState]
    channel_disclaimer: str | None

    def cta_actions(self) -> list[ConversionAction]:
        return [
            action
            for action in self.profile.conversion_actions
            if action.type in CTA_TYPES and action.medium == "web" and self._usable_target(action.target)
        ]

    def contact_actions(self) -> list[ConversionAction]:
        return [action for action in self.profile.conversion_actions if action.type in CONTACT_TYPES]

    def _usable_target(self, url: str) -> bool:
        if not on_domains(url, self.client_domains):
            return False
        status = self.link_statuses.get(normalise_url(url))
        return status is None or not status.broken

    @property
    def is_local_business(self) -> bool:
        identity = self.profile.identity
        return identity.geo_scope == "local" or identity.business_model.value in (
            "local_service",
            "multi_location_service",
        )

    @property
    def month_year(self) -> str:
        return self.now.strftime("%b %Y")
