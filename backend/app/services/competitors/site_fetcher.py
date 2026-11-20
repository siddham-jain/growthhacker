from dataclasses import replace
from typing import Any

from app.adapters.crawler.http_fetcher import DirectHttpFetcher
from app.core.registry import adapter_registry
from app.domain.web import FetchOutcome, FetchResult, SourceKind
from app.ports.clock import Clock
from app.ports.crawler import FetchContext
from app.services.website_intel.crawl.fetching import UserAgents
from app.services.website_intel.crawl.robots_service import RedisRobotsCache, RobotsService
from app.services.website_intel.extract.structured import json_ld_blocks


class CompetitorSiteFetcher:
    """the few public pages discovery reads on a rival's site (home, contact, about): robots first, no bypass."""

    def __init__(self, fetcher: DirectHttpFetcher, robots: RobotsService, clock: Clock) -> None:
        self._fetcher = fetcher
        self._robots = robots
        self._clock = clock

    async def fetch_page(self, url: str, ctx: FetchContext) -> FetchResult:
        rules, mode = await self._robots.rules(url, ctx)
        if not rules.can_fetch(url):
            return FetchResult(
                requested_url=url,
                final_url=url,
                source_kind=SourceKind.site_direct,
                outcome=FetchOutcome.robots_disallowed,
                status_code=None,
                fetched_at=self._clock.now(),
            )
        raw = await self._fetcher.fetch_raw(url, ctx.model_copy(update={"ua_mode": mode}))
        # only links are read here, and they sit in the page head and header, so an oversized page's
        # capped prefix is still usable (e.g. a multi-megabyte hospital home page)
        if raw.too_large and raw.verdict.fetch_outcome is FetchOutcome.ok:
            raw = replace(raw, too_large=False)
        result = self._fetcher.to_result(raw)
        # json-ld sameAs is often the only place a hospital site names its channel
        if result.html:
            result = result.model_copy(update={"json_ld": json_ld_blocks(result.html)})
        return result


@adapter_registry.register("site_crawler", "polite_crawler")
def build_competitor_site_fetcher(container: Any) -> CompetitorSiteFetcher:
    settings = container.settings
    agents = UserAgents(honest=settings.crawler.user_agent, compat=settings.crawler.compat_user_agent)
    fetcher = DirectHttpFetcher(container.http, user_agents=agents, clock=container.clock)
    robots = RobotsService(
        fetcher,
        token=settings.website_intel.robots_token,
        cache=RedisRobotsCache(container.redis, prefix=settings.redis.key_prefix),
        ttl_s=settings.website_intel.robots_cache_ttl_s,
    )
    return CompetitorSiteFetcher(fetcher, robots, container.clock)
