from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

import httpx

from app.adapters.context.wikidata import WikidataKnowledgeBase
from app.adapters.crawler.archive import CommonCrawlArchive, WaybackArchive
from app.adapters.crawler.http_fetcher import DirectHttpFetcher
from app.adapters.crawler.playwright_renderer import PlaywrightRenderer
from app.adapters.crawler.reader_proxy import JinaReader
from app.core.config import CrawlProfileSettings, Settings
from app.core.redis import RateLimiter
from app.domain.profile import BusinessProfile, BuyerQuestion
from app.domain.web import CrawlBudget
from app.ports.clock import Clock
from app.ports.context import BusinessLookup, ContextSource
from app.ports.crawler import Renderer
from app.ports.llm import LLMClient
from app.ports.progress import ProgressReporter
from app.ports.search import WebSearch
from app.ports.youtube import YouTubeGateway
from app.services.website_intel.acquisition.cascade import AcquisitionCascade, CascadeOutcome
from app.services.website_intel.acquisition.models import CascadeRequest, UserMaterial
from app.services.website_intel.acquisition.toolkit import ContextToolkit, build_sources
from app.services.website_intel.crawl.crawler import PoliteCrawler
from app.services.website_intel.crawl.fetching import UserAgents
from app.services.website_intel.crawl.page_types import PageTypeClassifier
from app.services.website_intel.crawl.politeness import HostGovernor
from app.services.website_intel.crawl.robots_service import RedisRobotsCache, RobotsService
from app.services.website_intel.extract.page_builder import PageExtractor
from app.services.website_intel.identity.locator import (
    ChannelResolverPort,
    DefaultBusinessLocator,
    LocatedBusinessDetail,
)
from app.services.website_intel.identity.site_from_channel import SiteFromChannel
from app.services.website_intel.inputs.site_resolver import SiteResolver
from app.services.website_intel.inputs.ssrf import SsrfGuard
from app.services.website_intel.profile.builder import BuildResult, ProfileBuilder
from app.services.website_intel.profile.llm import LlmGateway
from app.services.website_intel.profile.merger import Tombstone
from app.services.website_intel.profile.page_facts import MapCache
from app.services.website_intel.profile.page_selector import LlmPageSelector

CrawlProfileKind = Literal["client", "competitor"]


@dataclass
class WebsiteIntelDeps:
    settings: Settings
    clock: Clock
    http: httpx.AsyncClient
    redis: Any | None = None
    rate_limiter: RateLimiter | None = None
    web_search: WebSearch | None = None
    llm: LLMClient | None = None
    youtube: YouTubeGateway | None = None
    channel_resolver: ChannelResolverPort | None = None
    renderer: Renderer | None = None
    guard: SsrfGuard | None = None


def budget_for(profile: CrawlProfileSettings) -> CrawlBudget:
    return CrawlBudget(
        max_discovered=profile.discovery_fetch_budget,
        max_fetches=profile.fetch_budget,
        max_renders=profile.render_budget,
        max_pages_used=profile.map_page_budget,
        max_tavily_credits=profile.tavily_max_credits,
        max_archive_fetches=profile.archive_budget,
        max_reader_fetches=profile.reader_budget,
        wall_seconds=profile.wall_seconds,
    )


class WebsiteIntelService:
    """facade the pipeline stages call: locate → acquire (cascade) → build profile."""

    def __init__(self, deps: WebsiteIntelDeps, *, profile_kind: CrawlProfileKind = "client") -> None:
        self._deps = deps
        settings = deps.settings
        self.settings = settings.website_intel
        self.profile = getattr(self.settings, profile_kind)
        self.profile_kind = profile_kind
        self._agents = UserAgents(
            honest=settings.crawler.user_agent, compat=settings.crawler.compat_user_agent
        )
        self._extractor = PageExtractor(stored_text_char_cap=self.profile.stored_text_char_cap)
        self._page_types = PageTypeClassifier()
        self._fetcher = DirectHttpFetcher(
            deps.http,
            user_agents=self._agents,
            clock=deps.clock,
            guard=deps.guard,
            max_pdf_bytes=self.profile.max_pdf_bytes,
        )
        cache = (
            RedisRobotsCache(deps.redis, prefix=settings.redis.key_prefix) if deps.redis is not None else None
        )
        self._robots = RobotsService(
            self._fetcher,
            token=self.settings.robots_token,
            cache=cache,
            ttl_s=self.settings.robots_cache_ttl_s,
        )
        self._knowledge = WikidataKnowledgeBase(
            deps.http,
            api_url=self.settings.wikidata_api_url,
            summary_url=self.settings.wikipedia_summary_url,
            user_agent=settings.crawler.user_agent,
        )
        self._readers = [
            JinaReader(
                deps.http, base_url=self.settings.jina_reader_url, user_agent=settings.crawler.user_agent
            )
        ]
        self._archives = [
            WaybackArchive(
                deps.http,
                cdx_url=self.settings.wayback_cdx_url,
                raw_url_template=self.settings.wayback_raw_url,
                user_agent=settings.crawler.user_agent,
            ),
            CommonCrawlArchive(
                deps.http,
                collections_url=self.settings.common_crawl_index_url,
                data_url=self.settings.common_crawl_data_url,
                user_agent=settings.crawler.user_agent,
            ),
        ]
        self._renderer = deps.renderer or PlaywrightRenderer(
            user_agents=self._agents, clock=deps.clock, guard=deps.guard
        )

    def _web_search(self, client_id: UUID | None, run_id: UUID | None) -> WebSearch | None:
        search = self._deps.web_search
        attribute = getattr(search, "with_attribution", None)
        return attribute(client_id=client_id, run_id=run_id) if attribute is not None else search

    def locator(self, *, client_id: UUID | None, run_id: UUID | None) -> DefaultBusinessLocator:
        return DefaultBusinessLocator(
            site_resolver=SiteResolver(self._fetcher),
            extractor=self._extractor,
            knowledge=self._knowledge,
            channel_resolver=self._deps.channel_resolver,
            gateway=self._deps.youtube,
            site_from_channel=SiteFromChannel(self._fetcher),
            clock=self._deps.clock,
            web_search=self._web_search(client_id, run_id),
        )

    async def locate(
        self,
        lookup: BusinessLookup,
        reporter: ProgressReporter,
        *,
        client_id: UUID | None,
        run_id: UUID | None,
        scope_mode: str = "domain",
    ) -> LocatedBusinessDetail:
        return await self.locator(client_id=client_id, run_id=run_id).locate(
            lookup, reporter, client_id=client_id, run_id=run_id, scope_mode=scope_mode
        )

    def toolkit(
        self, *, client_id: UUID | None, run_id: UUID | None, llm: LlmGateway, country_hint: str | None
    ) -> ContextToolkit:
        crawler = PoliteCrawler(
            fetcher=self._fetcher,
            robots=self._robots,
            governor=HostGovernor(
                max_concurrency=self.profile.max_concurrency_per_host,
                min_delay_s=self.profile.min_delay_s,
                rate_limiter=self._deps.rate_limiter,
            ),
            extractor=self._extractor,
            settings=self.profile,
            clock=self._deps.clock,
            page_types=self._page_types,
            page_selector=LlmPageSelector(llm) if llm.available else None,
        )
        return ContextToolkit(
            settings=self.settings,
            profile=self.profile,
            clock=self._deps.clock,
            extractor=self._extractor,
            page_types=self._page_types,
            crawler=crawler,
            robots=self._robots,
            renderer=self._renderer,
            web_search=self._web_search(client_id, run_id),
            readers=list(self._readers),
            archives=list(self._archives),
            knowledge=self._knowledge,
            youtube=self._deps.youtube,
            client_id=client_id,
            run_id=run_id,
            country_hint=country_hint,
        )

    def llm(self, *, client_id: UUID | None, run_id: UUID | None, stage: str) -> LlmGateway:
        return LlmGateway(self._deps.llm, client_id=client_id, run_id=run_id, stage=stage)

    async def acquire(
        self,
        located: LocatedBusinessDetail,
        reporter: ProgressReporter,
        *,
        client_id: UUID,
        run_id: UUID | None,
        country_hint: str | None = None,
        user_material: Sequence[UserMaterial] = (),
        sources: Sequence[ContextSource] | None = None,
        tavily_credit_cap: float | None = None,
    ) -> CascadeOutcome:
        llm = self.llm(client_id=client_id, run_id=run_id, stage="crawl_site")
        toolkit = self.toolkit(client_id=client_id, run_id=run_id, llm=llm, country_hint=country_hint)
        request = CascadeRequest(
            client_id=client_id,
            run_id=run_id,
            business_name=located.business_name,
            site=located.site,
            channel_id=located.channel_id,
            country_hint=country_hint,
            missing_facets=frozenset(),
            knowledge_entity=located.knowledge_entity,
            user_material=list(user_material),
        )
        cascade = AcquisitionCascade(sources if sources is not None else build_sources(toolkit))
        budget = budget_for(self.profile)
        if tavily_credit_cap is not None:
            budget = budget.model_copy(
                update={"max_tavily_credits": max(0, min(budget.max_tavily_credits, int(tavily_credit_cap)))}
            )
        return await cascade.run(request, budget, reporter)

    async def build(
        self,
        *,
        client_id: UUID,
        run_id: UUID | None,
        located: LocatedBusinessDetail | None,
        outcome: CascadeOutcome,
        reporter: ProgressReporter,
        version: int,
        previous: BusinessProfile | None = None,
        tombstones: Sequence[Tombstone] = (),
        existing_questions: Sequence[BuyerQuestion] = (),
        crawl_run_id: UUID | None = None,
        country_hint: str | None = None,
        map_cache: MapCache | None = None,
    ) -> BuildResult:
        builder = ProfileBuilder(
            llm=self.llm(client_id=client_id, run_id=run_id, stage="build_profile"),
            settings=self.settings,
            profile_settings=self.profile,
            clock=self._deps.clock,
            map_cache=map_cache,
        )
        return await builder.build(
            client_id=client_id,
            outcome=outcome,
            located=located,
            reporter=reporter,
            version=version,
            previous=previous,
            tombstones=tombstones,
            existing_questions=existing_questions,
            crawl_run_id=crawl_run_id,
            country_hint=country_hint,
        )

    async def aclose(self) -> None:
        close = getattr(self._renderer, "aclose", None)
        if close is not None:
            await close()


def deps_from_container(container: Any) -> WebsiteIntelDeps:
    """resolves optional collaborators; a missing provider disables the tiers that need it."""

    def optional(port: str) -> Any:
        try:
            return container.adapter(port)
        except (LookupError, KeyError):
            return None

    data_layer = optional("youtube_data_layer")
    return WebsiteIntelDeps(
        settings=container.settings,
        clock=container.clock,
        http=container.http,
        redis=container.redis,
        rate_limiter=container.rate_limiter,
        web_search=optional("web_search"),
        llm=optional("llm"),
        youtube=optional("youtube"),
        channel_resolver=data_layer.resolver if data_layer is not None else None,
    )
