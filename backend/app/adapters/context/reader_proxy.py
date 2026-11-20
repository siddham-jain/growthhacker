from dataclasses import dataclass, field
from typing import ClassVar

from app.adapters.search.errors import WebSearchError
from app.core.registry import context_source_registry
from app.domain.web import BlockReason, CoverageFacet, CrawlBudget, SourceKind
from app.ports.context import BlockedAttempt
from app.ports.progress import ProgressReporter
from app.ports.search import ExtractRequest, WebSearch
from app.services.website_intel.acquisition.models import CascadeRequest, SourceHarvest, harvest
from app.services.website_intel.acquisition.ports import MarkdownReader
from app.services.website_intel.acquisition.targets import target_urls
from app.services.website_intel.acquisition.toolkit import ContextToolkit
from app.services.website_intel.models import ExtractedPage, PageVerdict

ADVANCED_CREDITS_PER_BATCH = 2
TAVILY_URLS = 5
MIN_READER_CHARS = 200
EXPANSION_PAGES = 6


@dataclass
class _Batch:
    pages: list[ExtractedPage] = field(default_factory=list)
    blocked: list[BlockedAttempt] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    credits: float = 0.0


class ReaderProxySource:
    """tier 3: tavily extract (advanced), then markdown readers; third-party egress, never the origin."""

    source_kind: ClassVar[SourceKind] = SourceKind.reader_proxy
    tier: ClassVar[int] = 3

    def __init__(
        self, web_search: WebSearch | None, readers: list[MarkdownReader], toolkit: ContextToolkit
    ) -> None:
        self._search = web_search
        self._readers = readers
        self._toolkit = toolkit

    def applicable(self, request: CascadeRequest) -> bool:
        return request.site is not None and bool(request.missing_facets)

    async def acquire(
        self, request: CascadeRequest, budget: CrawlBudget, reporter: ProgressReporter
    ) -> SourceHarvest:
        limit = min(budget.max_reader_fetches, self._toolkit.profile.reader_budget)
        result = _Batch()
        urls = target_urls(request, self._toolkit.page_types, limit=limit)
        await self._read(request, urls, budget.max_tavily_credits, result)
        if not request.home_covered and result.pages:
            # the site tiers got nothing, so read one page per key facet linked from the copies we have
            expanded = request.model_copy(
                update={
                    "home_covered": True,
                    "missing_facets": frozenset(CoverageFacet),
                    "fetched_urls": [*request.fetched_urls, *urls],
                }
            )
            links = [link.url for page in result.pages for link in page.details.links]
            extra = target_urls(
                expanded,
                self._toolkit.page_types,
                limit=min(EXPANSION_PAGES, limit - len(urls)),
                extra=links,
                include_blocked=False,
            )
            await self._read(request, extra, budget.max_tavily_credits - result.credits, result)
            urls = [*urls, *extra]
        return harvest(
            self.source_kind,
            result.pages,
            blocked=result.blocked,
            tavily_credits=result.credits,
            fetches=len(urls),
            notes=result.notes,
        )

    async def _read(
        self, request: CascadeRequest, urls: list[str], credits_left: float, result: "_Batch"
    ) -> None:
        pending = list(urls)
        if self._search is not None and pending and credits_left >= ADVANCED_CREDITS_PER_BATCH:
            try:
                response = await self._search.extract(
                    ExtractRequest(urls=pending[:TAVILY_URLS], depth="advanced", format="markdown")
                )
            except WebSearchError as error:
                result.notes.append(f"tavily extract unavailable: {error.kind}")
            else:
                result.credits += response.credits_used
                for document in response.results:
                    page = self._page(request, document.url, document.url, None, document.raw_content)
                    if page is None:
                        result.blocked.append(
                            BlockedAttempt(
                                url=document.url, source_kind=self.source_kind, reason=BlockReason.waf_block
                            )
                        )
                        continue
                    result.pages.append(page)
                    if document.url in pending:
                        pending.remove(document.url)
        for reader in self._readers:
            for url in list(pending):
                document = await reader.read(url)
                if document.verdict is not PageVerdict.ok or len(document.markdown) < MIN_READER_CHARS:
                    reason = document.verdict.block_reason or BlockReason.other
                    result.blocked.append(
                        BlockedAttempt(url=url, source_kind=self.source_kind, reason=reason)
                    )
                    continue
                page = self._page(request, url, document.final_url, document.title, document.markdown)
                if page is not None:
                    result.pages.append(page)
                    pending.remove(url)

    def _page(
        self, request: CascadeRequest, url: str, final_url: str, title: str | None, markdown: str
    ) -> ExtractedPage | None:
        verdict = self._toolkit.classifier.classify(
            status_code=200,
            headers={},
            body=f"{title or ''}\n{markdown[:20_000]}",
            content_type="text/markdown",
        )
        if verdict is not PageVerdict.ok or len(markdown) < MIN_READER_CHARS:
            return None
        return self._toolkit.typed(
            self._toolkit.extractor.from_markdown(
                markdown=markdown,
                requested_url=url,
                final_url=final_url,
                title=title,
                site=request.site,
                source_kind=self.source_kind,
                fetched_at=self._toolkit.clock.now(),
                country_hint=request.country_hint,
            )
        )


@context_source_registry.register(SourceKind.reader_proxy.value)
def build_reader_proxy(toolkit: ContextToolkit) -> ReaderProxySource | None:
    if toolkit.web_search is None and not toolkit.readers:
        return None
    return ReaderProxySource(toolkit.web_search, toolkit.readers, toolkit)
