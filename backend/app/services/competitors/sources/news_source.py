import asyncio

from app.ports.search import WebSearchHit, WebSearchQuery
from app.services.competitors.classify.geo import TAVILY_COUNTRY_NAMES
from app.services.competitors.context import DiscoveryContext
from app.services.competitors.model import RawCandidate
from app.services.competitors.providers import search_web
from app.services.competitors.registries import candidate_generator_registry
from app.services.competitors.sources.base import SourceDeps
from app.services.competitors.sources.brief import category
from app.services.competitors.sources.extraction import SearchEntityExtractor


@candidate_generator_registry.register("rediscovery_news")
class NewEntrantNewsSource:
    kind = "rediscovery_news"
    used_in_rediscovery = True

    def __init__(self, deps: SourceDeps) -> None:
        self.web_search = deps.web_search
        self.extractor = SearchEntityExtractor(deps)

    def enabled(self, ctx: DiscoveryContext) -> bool:
        return (
            self.web_search is not None
            and ctx.trigger in {"scheduled", "profile_change"}
            and ctx.source_enabled(self.kind)
        )

    def queries(self, ctx: DiscoveryContext) -> list[WebSearchQuery]:
        profile = ctx.profile
        country = TAVILY_COUNTRY_NAMES.get(ctx.market or "", ctx.market or "")
        texts = [f"new {category(profile)} launch {country}".strip()]
        if profile.locations.cities:
            texts.append(f"{category(profile)} {profile.locations.cities[0]} opens")
        return [
            WebSearchQuery(query=text, topic="news", time_range="month", max_results=8, search_depth="basic")
            for text in texts
        ]

    async def generate(self, ctx: DiscoveryContext) -> list[RawCandidate]:
        responses = await asyncio.gather(
            *(search_web(ctx, self.web_search, query) for query in self.queries(ctx))
        )
        hits: list[WebSearchHit] = [hit for response in responses if response for hit in response.hits]
        return await self.extractor.extract(ctx, hits, self.kind, ctx.settings.source_caps.rediscovery_news)
