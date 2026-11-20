from typing import ClassVar

from app.adapters.search.errors import WebSearchError
from app.core.registry import context_source_registry
from app.domain.web import CoverageFacet, CrawlBudget, SourceKind
from app.ports.progress import ProgressReporter
from app.ports.search import WebSearch, WebSearchQuery
from app.services.website_intel.acquisition.models import CascadeRequest, SourceHarvest, harvest
from app.services.website_intel.acquisition.toolkit import ContextToolkit
from app.services.website_intel.inputs.urls import InvalidUrlError, normalize_url
from app.services.website_intel.models import ExtractedPage

FACET_QUERIES: dict[CoverageFacet, str] = {
    CoverageFacet.offerings: '"{name}" services',
    CoverageFacet.conversion_actions: '"{name}" pricing',
    CoverageFacet.about: '"{name}" reviews',
    CoverageFacet.contact_locations: "{name} locations",
    CoverageFacet.home: '"{name}"',
}
MAX_RESULTS = 3
MAX_MENTION_CHARS = 12_000


class WebMentionsSource:
    """tier 7: what third parties say; facts are labelled third-party and never auto-confirmed."""

    source_kind: ClassVar[SourceKind] = SourceKind.web_mentions
    tier: ClassVar[int] = 7

    def __init__(self, web_search: WebSearch, toolkit: ContextToolkit) -> None:
        self._search = web_search
        self._toolkit = toolkit

    def applicable(self, request: CascadeRequest) -> bool:
        return bool(request.business_name) and bool(request.missing_facets - {CoverageFacet.home})

    async def acquire(
        self, request: CascadeRequest, budget: CrawlBudget, reporter: ProgressReporter
    ) -> SourceHarvest:
        name = request.business_name or ""
        own = [request.site.registrable_domain] if request.site else []
        facets = [
            facet
            for facet in FACET_QUERIES
            if facet in request.missing_facets and facet is not CoverageFacet.home
        ]
        credits = 0.0
        pages: list[ExtractedPage] = []
        notes: list[str] = []
        for facet in facets[: self._toolkit.settings.web_mention_queries]:
            if credits + 1 > budget.max_tavily_credits:
                break
            try:
                response = await self._search.search(
                    WebSearchQuery(
                        query=FACET_QUERIES[facet].format(name=name),
                        exclude_domains=own,
                        include_raw_content=True,
                        max_results=MAX_RESULTS,
                        country=None,
                    )
                )
            except WebSearchError as error:
                notes.append(f"tavily search unavailable: {error.kind}")
                break
            credits += response.credits_used
            for hit in response.hits:
                try:
                    url = normalize_url(hit.url)
                except InvalidUrlError:
                    continue
                body = (hit.raw_content or hit.snippet)[:MAX_MENTION_CHARS]
                if name.casefold().split()[0] not in body.casefold():
                    continue
                page = self._toolkit.extractor.from_markdown(
                    markdown=body,
                    requested_url=url,
                    final_url=url,
                    title=hit.title or None,
                    site=None,
                    source_kind=self.source_kind,
                    fetched_at=self._toolkit.clock.now(),
                    country_hint=request.country_hint,
                )
                # third-party pages never contribute the client's own conversion actions
                details = page.details.model_copy(update={"ctas": []})
                typed = page.page.model_copy(update={"page_type": "other"})
                pages.append(page.model_copy(update={"page": typed, "details": details}))
        return harvest(self.source_kind, pages, tavily_credits=credits, notes=notes)


@context_source_registry.register(SourceKind.web_mentions.value)
def build_web_mentions(toolkit: ContextToolkit) -> WebMentionsSource | None:
    return WebMentionsSource(toolkit.web_search, toolkit) if toolkit.web_search is not None else None
