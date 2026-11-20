from datetime import timedelta
from typing import ClassVar

from app.core.registry import context_source_registry
from app.domain.web import CoverageFacet, CrawlBudget, SourceKind
from app.ports.progress import ProgressReporter
from app.services.website_intel.acquisition.models import CascadeRequest, SourceHarvest, harvest
from app.services.website_intel.acquisition.ports import WebArchive
from app.services.website_intel.acquisition.targets import target_urls
from app.services.website_intel.acquisition.toolkit import ContextToolkit
from app.services.website_intel.models import ExtractedPage, PageVerdict


class ArchiveSource:
    """tier 5: latest archived 200 captures (wayback, then common crawl) labelled with their snapshot date."""

    source_kind: ClassVar[SourceKind] = SourceKind.archive
    tier: ClassVar[int] = 5

    def __init__(self, archives: list[WebArchive], toolkit: ContextToolkit) -> None:
        self._archives = archives
        self._toolkit = toolkit

    def applicable(self, request: CascadeRequest) -> bool:
        if request.site is None or not request.missing_facets:
            return False
        site_pages = [url for url in request.fetched_urls if url.startswith(request.site.canonical_origin)]
        return not request.home_covered or CoverageFacet.offerings in request.missing_facets or not site_pages

    async def acquire(
        self, request: CascadeRequest, budget: CrawlBudget, reporter: ProgressReporter
    ) -> SourceHarvest:
        site = request.site
        if site is None:
            return harvest(self.source_kind, [])
        limit = min(budget.max_archive_fetches, self._toolkit.profile.archive_budget)
        urls = target_urls(request, self._toolkit.page_types, limit=limit)
        stale_after = timedelta(days=self._toolkit.settings.archive_stale_after_days)
        now = self._toolkit.clock.now()
        pages: list[ExtractedPage] = []
        notes: list[str] = []
        lookups = 0
        for url in urls:
            for archive in self._archives:
                lookups += 1
                captured = await archive.latest(url)
                if captured is None:
                    continue
                verdict = self._toolkit.classifier.classify(
                    status_code=captured.status_code,
                    headers={},
                    body=captured.html,
                    content_type=captured.content_type,
                )
                if verdict is not PageVerdict.ok:
                    continue
                page = self._toolkit.typed(
                    self._toolkit.extractor.from_html(
                        html=captured.html,
                        requested_url=url,
                        final_url=captured.original_url,
                        site=site,
                        source_kind=self.source_kind,
                        status_code=captured.status_code,
                        content_type=captured.content_type,
                        fetched_at=now,
                        snapshot_at=captured.snapshot_at,
                        country_hint=request.country_hint,
                    )
                )
                pages.append(page)
                if now - captured.snapshot_at > stale_after:
                    notes.append(
                        f"archived copy of {url} is from {captured.snapshot_at.date()}; facts flagged stale"
                    )
                break
        return harvest(self.source_kind, pages, fetches=lookups, notes=notes)


@context_source_registry.register(SourceKind.archive.value)
def build_archive(toolkit: ContextToolkit) -> ArchiveSource | None:
    return ArchiveSource(toolkit.archives, toolkit) if toolkit.archives else None
