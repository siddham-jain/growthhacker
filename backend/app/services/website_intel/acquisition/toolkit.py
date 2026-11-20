from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from app.core.config import CrawlProfileSettings, WebsiteIntelSettings
from app.core.registry import Registry, context_source_registry
from app.ports.clock import Clock
from app.ports.context import ContextSource
from app.ports.crawler import Renderer
from app.ports.search import WebSearch
from app.ports.youtube import YouTubeGateway
from app.services.website_intel.acquisition.ports import MarkdownReader, WebArchive
from app.services.website_intel.crawl.blocks import BlockClassifier
from app.services.website_intel.crawl.crawler import PoliteCrawler
from app.services.website_intel.crawl.page_types import PageTypeClassifier
from app.services.website_intel.crawl.robots_service import RobotsService
from app.services.website_intel.extract.page_builder import PageExtractor
from app.services.website_intel.identity.knowledge import KnowledgeBase
from app.services.website_intel.models import ExtractedPage

SourceFactory = Callable[["ContextToolkit"], ContextSource | None]


@dataclass
class ContextToolkit:
    # shared collaborators each ContextSource factory picks from; a missing one disables that tier
    settings: WebsiteIntelSettings
    profile: CrawlProfileSettings
    clock: Clock
    extractor: PageExtractor
    page_types: PageTypeClassifier = field(default_factory=PageTypeClassifier)
    classifier: BlockClassifier = field(default_factory=BlockClassifier)
    crawler: PoliteCrawler | None = None
    robots: RobotsService | None = None
    renderer: Renderer | None = None
    web_search: WebSearch | None = None
    readers: list[MarkdownReader] = field(default_factory=list)
    archives: list[WebArchive] = field(default_factory=list)
    knowledge: KnowledgeBase | None = None
    youtube: YouTubeGateway | None = None
    client_id: UUID | None = None
    run_id: UUID | None = None
    country_hint: str | None = None

    def typed(self, page: ExtractedPage) -> ExtractedPage:
        json_ld_types = [str(block.get("@type")) for block in page.page.json_ld if block.get("@type")]
        page_type = self.page_types.classify(
            page.page.url,
            title=page.page.title or "",
            h1=page.page.h1 or "",
            json_ld_types=json_ld_types,
            is_home=page.page.url.rstrip("/").count("/") <= 2,
        )
        return page.model_copy(update={"page": page.page.model_copy(update={"page_type": page_type.value})})


def build_sources(
    toolkit: ContextToolkit, registry: Registry[Any] = context_source_registry
) -> list[ContextSource]:
    disabled = set(toolkit.settings.disabled_sources)
    sources = []
    for key, factory in registry.all().items():
        if key in disabled:
            continue
        source = factory(toolkit)
        if source is not None:
            sources.append(source)
    return sources
