from typing import ClassVar, Literal, Protocol
from uuid import UUID

from pydantic import model_validator

from app.domain.common import ChannelId, CountryCode, DomainModel
from app.domain.web import BlockReason, CoverageFacet, CrawlBudget, CrawledPage, SiteRef, SourceKind
from app.ports.progress import ProgressReporter


class AcquisitionRequest(DomainModel):
    client_id: UUID
    run_id: UUID | None
    business_name: str | None
    site: SiteRef | None
    channel_id: ChannelId | None
    country_hint: CountryCode | None = None
    missing_facets: frozenset[CoverageFacet]


class BlockedAttempt(DomainModel):
    url: str
    source_kind: SourceKind
    reason: BlockReason


class AcquisitionResult(DomainModel):
    source_kind: SourceKind
    pages: list[CrawledPage]
    covered_facets: frozenset[CoverageFacet] = frozenset()
    blocked: list[BlockedAttempt] = []
    tavily_credits: float = 0.0
    fetches: int = 0


class ContextSource(Protocol):
    # one tier of the acquisition cascade; adding a source is adding a class
    source_kind: ClassVar[SourceKind]
    tier: ClassVar[int]

    def applicable(self, request: AcquisitionRequest) -> bool: ...

    async def acquire(
        self, request: AcquisitionRequest, budget: CrawlBudget, reporter: ProgressReporter
    ) -> AcquisitionResult: ...


class BusinessLookup(DomainModel):
    business_name: str | None = None
    website_url: str | None = None
    channel_ref: str | None = None
    country_hint: CountryCode | None = None

    @model_validator(mode="after")
    def _needs_one_identifier(self) -> "BusinessLookup":
        if not (self.business_name or self.website_url or self.channel_ref):
            raise ValueError("provide a website, a channel or a business name")
        return self


class LocatedBusiness(DomainModel):
    business_name: str | None
    site: SiteRef | None
    site_basis: Literal["user_input", "knowledge_base", "web_search", "channel_link"] | None
    channel_id: ChannelId | None
    channel_basis: Literal["user_input", "website_link", "web_search", "handle_lookup"] | None
    needs_user_confirmation: bool
    notes: list[str] = []


class BusinessLocator(Protocol):
    # resolves the official site and channel from any one identifier; ownership goes through 02's verifier
    async def locate(self, lookup: BusinessLookup, reporter: ProgressReporter) -> LocatedBusiness: ...
