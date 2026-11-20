from typing import ClassVar

from app.core.registry import context_source_registry
from app.domain.web import BlockReason, CrawlBudget, FetchOutcome, SourceKind, UserAgentMode
from app.ports.context import BlockedAttempt
from app.ports.crawler import FetchContext, Renderer
from app.ports.progress import ProgressReporter
from app.services.website_intel.acquisition.models import (
    CascadeRequest,
    RobotsSnapshot,
    SourceHarvest,
    harvest,
)
from app.services.website_intel.acquisition.targets import RETRYABLE_BLOCKS, target_urls
from app.services.website_intel.acquisition.toolkit import ContextToolkit
from app.services.website_intel.crawl.robots_service import RobotsService
from app.services.website_intel.inputs.ssrf import DnsLookupError, UnsafeTargetError
from app.services.website_intel.models import ExtractedPage


class SiteRenderedSource:
    """tier 2: a real headless browser for javascript shells and browser-gated pages; robots obeyed."""

    source_kind: ClassVar[SourceKind] = SourceKind.site_rendered
    tier: ClassVar[int] = 2

    def __init__(self, renderer: Renderer, robots: RobotsService | None, toolkit: ContextToolkit) -> None:
        self._renderer = renderer
        self._robots = robots
        self._toolkit = toolkit

    def applicable(self, request: CascadeRequest) -> bool:
        if request.site is None:
            return False
        blocked_on_site = any(
            attempt.source_kind is SourceKind.site_direct and attempt.reason in RETRYABLE_BLOCKS
            for attempt in request.prior_blocked
        )
        return blocked_on_site or not request.home_covered

    async def acquire(
        self, request: CascadeRequest, budget: CrawlBudget, reporter: ProgressReporter
    ) -> SourceHarvest:
        site = request.site
        if site is None:
            return harvest(self.source_kind, [])
        ctx = FetchContext(client_id=request.client_id, run_id=request.run_id, ua_mode=UserAgentMode.compat)
        robots = request.robots
        if robots is None and self._robots is not None:
            rules, _ = await self._robots.rules(site.canonical_origin, ctx)
            robots = RobotsSnapshot(status_code=rules.status_code, body=rules.body, token=rules.token)
            request = request.model_copy(update={"robots": robots})
        rules = robots.rules() if robots is not None else None
        limit = min(budget.max_renders, self._toolkit.profile.render_budget)
        queue = target_urls(request, self._toolkit.page_types, limit=limit)
        pages: list[ExtractedPage] = []
        blocked: list[BlockedAttempt] = []
        renders = 0
        while queue and renders < limit:
            url = queue.pop(0)
            if rules is not None and not rules.can_fetch(url):
                continue
            renders += 1
            try:
                result = await self._renderer.render(url, ctx)
            except (UnsafeTargetError, DnsLookupError):
                continue
            if result.outcome is not FetchOutcome.ok or not result.html:
                blocked.append(
                    BlockedAttempt(
                        url=url, source_kind=self.source_kind, reason=result.block_reason or BlockReason.other
                    )
                )
                continue
            page = self._toolkit.typed(
                self._toolkit.extractor.from_html(
                    html=result.html,
                    requested_url=url,
                    final_url=result.final_url,
                    site=site,
                    source_kind=self.source_kind,
                    status_code=result.status_code,
                    content_type=result.content_type,
                    fetched_at=result.fetched_at,
                    country_hint=request.country_hint,
                )
            )
            pages.append(page)
            if len(pages) == 1:
                extra = target_urls(
                    request.model_copy(
                        update={"home_covered": True, "fetched_urls": [url, *request.fetched_urls]}
                    ),
                    self._toolkit.page_types,
                    limit=limit - renders,
                    extra=[link.url for link in page.details.links],
                    include_blocked=False,
                )
                queue.extend(candidate for candidate in extra if candidate not in queue)
        return harvest(self.source_kind, pages, blocked=blocked, fetches=renders, robots=robots)


@context_source_registry.register(SourceKind.site_rendered.value)
def build_site_rendered(toolkit: ContextToolkit) -> SiteRenderedSource | None:
    if toolkit.renderer is None:
        return None
    return SiteRenderedSource(toolkit.renderer, toolkit.robots, toolkit)
