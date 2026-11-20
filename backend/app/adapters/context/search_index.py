from typing import ClassVar

from app.adapters.search.errors import WebSearchError
from app.core.registry import context_source_registry
from app.domain.web import CoverageFacet, CrawlBudget, SourceKind
from app.ports.progress import ProgressReporter
from app.ports.search import SiteMapRequest, WebSearch, WebSearchQuery
from app.services.website_intel.acquisition.models import CascadeRequest, SourceHarvest, harvest
from app.services.website_intel.acquisition.toolkit import ContextToolkit
from app.services.website_intel.inputs.urls import InvalidUrlError, normalize_url, same_registrable_domain
from app.services.website_intel.models import ExtractedPage, InventoryUrl, PageVerdict

FACET_QUERIES: dict[CoverageFacet, str] = {
    CoverageFacet.home: "{name}",
    CoverageFacet.offerings: "{name} services treatments products",
    CoverageFacet.about: "about {name}",
    CoverageFacet.contact_locations: "{name} contact address locations",
    CoverageFacet.conversion_actions: "{name} book appointment contact",
}
FACET_ORDER = (
    CoverageFacet.offerings,
    CoverageFacet.about,
    CoverageFacet.contact_locations,
    CoverageFacet.conversion_actions,
    CoverageFacet.home,
)
MAP_LIMIT = 50
MIN_RAW_CHARS = 200


class SearchIndexSource:
    """tier 4: page bodies and urls from tavily's index for our own domain, without touching the origin."""

    source_kind: ClassVar[SourceKind] = SourceKind.search_index
    tier: ClassVar[int] = 4

    def __init__(self, web_search: WebSearch, toolkit: ContextToolkit) -> None:
        self._search = web_search
        self._toolkit = toolkit

    def applicable(self, request: CascadeRequest) -> bool:
        return request.site is not None and bool(request.missing_facets)

    async def acquire(
        self, request: CascadeRequest, budget: CrawlBudget, reporter: ProgressReporter
    ) -> SourceHarvest:
        site = request.site
        if site is None:
            return harvest(self.source_kind, [])
        name = request.business_name or site.registrable_domain
        credits = 0.0
        pages: list[ExtractedPage] = []
        notes: list[str] = []
        seen = set(request.fetched_urls)
        facets = [facet for facet in FACET_ORDER if facet in request.missing_facets]
        for facet in facets[: self._toolkit.settings.search_index_queries]:
            if credits + 1 > budget.max_tavily_credits:
                break
            query = WebSearchQuery(
                query=FACET_QUERIES[facet].format(name=name),
                include_domains=[site.registrable_domain],
                include_raw_content=True,
                max_results=5,
            )
            try:
                response = await self._search.search(query)
            except WebSearchError as error:
                notes.append(f"tavily search unavailable: {error.kind}")
                break
            credits += response.credits_used
            for hit in response.hits:
                try:
                    url = normalize_url(hit.url)
                except InvalidUrlError:
                    continue
                if url in seen or not same_registrable_domain(url, site.registrable_domain):
                    continue
                if (
                    not request.robots_allows(url)
                    or not hit.raw_content
                    or len(hit.raw_content) < MIN_RAW_CHARS
                ):
                    continue
                page = self._page(request, url, hit.title, hit.raw_content)
                if page is not None:
                    seen.add(url)
                    pages.append(page)
        inventory: list[InventoryUrl] = []
        if not request.candidate_urls and credits + MAP_LIMIT / 10 <= budget.max_tavily_credits:
            try:
                mapped = await self._search.map(
                    SiteMapRequest(url=site.canonical_origin, max_depth=2, limit=MAP_LIMIT)
                )
            except WebSearchError as error:
                notes.append(f"tavily map unavailable: {error.kind}")
            else:
                credits += mapped.credits_used
                inventory = [
                    InventoryUrl(
                        url=url, page_type=self._toolkit.page_types.classify(url), source="tavily_map"
                    )
                    for url in mapped.urls
                    if same_registrable_domain(url, site.registrable_domain) and request.robots_allows(url)
                ]
        return harvest(
            self.source_kind,
            pages,
            tavily_credits=credits,
            inventory=inventory,
            remaining_urls=[item.url for item in inventory],
            notes=notes,
        )

    def _page(self, request: CascadeRequest, url: str, title: str, markdown: str) -> ExtractedPage | None:
        verdict = self._toolkit.classifier.classify(
            status_code=200, headers={}, body=f"{title}\n{markdown[:20_000]}", content_type="text/markdown"
        )
        if verdict is not PageVerdict.ok:
            return None
        return self._toolkit.typed(
            self._toolkit.extractor.from_markdown(
                markdown=markdown,
                requested_url=url,
                final_url=url,
                title=title or None,
                site=request.site,
                source_kind=self.source_kind,
                fetched_at=self._toolkit.clock.now(),
                country_hint=request.country_hint,
            )
        )


@context_source_registry.register(SourceKind.search_index.value)
def build_search_index(toolkit: ContextToolkit) -> SearchIndexSource | None:
    return SearchIndexSource(toolkit.web_search, toolkit) if toolkit.web_search is not None else None
