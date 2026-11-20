from typing import ClassVar

from app.core.registry import context_source_registry
from app.domain.web import CrawlBudget, SourceKind
from app.ports.progress import ProgressReporter
from app.services.website_intel.acquisition.models import (
    CascadeRequest,
    RobotsSnapshot,
    SourceHarvest,
    harvest,
)
from app.services.website_intel.acquisition.toolkit import ContextToolkit
from app.services.website_intel.crawl.crawler import PoliteCrawler


class SiteDirectSource:
    """tier 1: our own polite crawler, honest agent first, compat agent after a waf 401/403."""

    source_kind: ClassVar[SourceKind] = SourceKind.site_direct
    tier: ClassVar[int] = 1

    def __init__(self, crawler: PoliteCrawler, toolkit: ContextToolkit) -> None:
        self._crawler = crawler
        self._toolkit = toolkit

    def applicable(self, request: CascadeRequest) -> bool:
        return request.site is not None

    async def acquire(
        self, request: CascadeRequest, budget: CrawlBudget, reporter: ProgressReporter
    ) -> SourceHarvest:
        if request.site is None:
            return harvest(self.source_kind, [])
        result = await self._crawler.harvest(
            request.site,
            budget,
            reporter,
            client_id=request.client_id,
            run_id=request.run_id,
            country_hint=request.country_hint,
            business_name=request.business_name,
        )
        robots = result.robots
        return harvest(
            self.source_kind,
            result.pages,
            blocked=result.blocked,
            fetches=result.stats.get("fetched", 0),
            inventory=result.inventory,
            templates=result.templates,
            nav=result.nav,
            remaining_urls=result.remaining,
            robots=RobotsSnapshot(status_code=robots.status_code, body=robots.body, token=robots.token)
            if robots is not None
            else None,
            ua_mode=result.ua_mode,
            notes=result.notes,
            stats=dict(result.stats),
        )


@context_source_registry.register(SourceKind.site_direct.value)
def build_site_direct(toolkit: ContextToolkit) -> SiteDirectSource | None:
    return SiteDirectSource(toolkit.crawler, toolkit) if toolkit.crawler is not None else None
