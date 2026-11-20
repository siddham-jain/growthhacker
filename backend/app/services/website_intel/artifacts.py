import gzip
from collections import Counter
from uuid import UUID

from app.domain.common import DomainModel
from app.domain.web import CoverageFacet, SourceKind, UserAgentMode
from app.ports.context import BlockedAttempt
from app.services.website_intel.acquisition.cascade import CascadeOutcome, TierReport
from app.services.website_intel.acquisition.models import RobotsSnapshot
from app.services.website_intel.identity.knowledge import KbEntity
from app.services.website_intel.models import ExtractedPage, InventoryUrl, NavItem, TemplateSummary


class TierSummary(DomainModel):
    tier: int
    source_kind: SourceKind
    pages: int
    blocked: int
    credits: float
    covered: list[CoverageFacet] = []
    notes: list[str] = []


class SiteAcquisition(DomainModel):
    # crawl_site output; carries youtube channel text, so the artifact counts as api data (28-day rule)
    website_id: UUID | None = None
    crawl_run_id: UUID | None = None
    pages: list[ExtractedPage] = []
    blocked: list[BlockedAttempt] = []
    covered: list[CoverageFacet] = []
    tiers: list[TierSummary] = []
    inventory: list[InventoryUrl] = []
    candidates: list[str] = []
    templates: list[TemplateSummary] = []
    nav: list[NavItem] = []
    robots: RobotsSnapshot | None = None
    ua_mode: UserAgentMode = UserAgentMode.honest
    knowledge_entity: KbEntity | None = None
    tavily_credits: float = 0.0
    fetches: int = 0
    stats: dict[str, int] = {}
    notes: list[str] = []
    # true when pages and inventory live gzipped on the crawl run row instead of in this artifact
    stored: bool = False

    def compressed(self) -> bytes:
        return gzip.compress(self.model_dump_json().encode("utf-8"))

    @classmethod
    def decompressed(cls, payload: bytes) -> "SiteAcquisition":
        return cls.model_validate_json(gzip.decompress(payload))

    def pointer(self) -> "SiteAcquisition":
        # run artifacts hold pointers, not bulk data; build_profile reloads the rest from the crawl run
        return self.model_copy(update={"pages": [], "inventory": [], "stored": True})

    @classmethod
    def from_outcome(
        cls, outcome: CascadeOutcome, *, website_id: UUID | None = None, crawl_run_id: UUID | None = None
    ) -> "SiteAcquisition":
        return cls(
            website_id=website_id,
            crawl_run_id=crawl_run_id,
            pages=outcome.pages,
            blocked=outcome.blocked,
            covered=sorted(outcome.covered),
            tiers=[
                TierSummary(
                    tier=tier.tier,
                    source_kind=tier.source_kind,
                    pages=tier.pages,
                    blocked=tier.blocked,
                    credits=tier.credits,
                    covered=tier.covered,
                    notes=tier.notes,
                )
                for tier in outcome.tiers
            ],
            inventory=outcome.inventory,
            candidates=outcome.candidates[:500],
            templates=outcome.templates,
            nav=outcome.nav,
            robots=outcome.robots,
            ua_mode=outcome.ua_mode,
            knowledge_entity=outcome.knowledge_entity,
            tavily_credits=outcome.tavily_credits,
            fetches=outcome.fetches,
            stats=dict(outcome.stats),
            notes=outcome.notes,
        )

    def to_outcome(self) -> CascadeOutcome:
        return CascadeOutcome(
            pages=list(self.pages),
            blocked=list(self.blocked),
            covered=frozenset(self.covered),
            tiers=[
                TierReport(
                    tier=tier.tier,
                    source_kind=tier.source_kind,
                    pages=tier.pages,
                    blocked=tier.blocked,
                    covered=tier.covered,
                    credits=tier.credits,
                    notes=tier.notes,
                )
                for tier in self.tiers
            ],
            inventory=list(self.inventory),
            candidates=list(self.candidates),
            templates=list(self.templates),
            nav=list(self.nav),
            robots=self.robots,
            ua_mode=self.ua_mode,
            knowledge_entity=self.knowledge_entity,
            tavily_credits=self.tavily_credits,
            fetches=self.fetches,
            stats=Counter(self.stats),
            notes=list(self.notes),
        )
