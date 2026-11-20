import asyncio
from collections.abc import Callable

from app.domain.profile import BusinessModel, BusinessProfile
from app.ports.search import WebSearchHit, WebSearchQuery
from app.services.competitors.classify.geo import TAVILY_COUNTRY_NAMES
from app.services.competitors.context import DiscoveryContext
from app.services.competitors.model import RawCandidate
from app.services.competitors.providers import search_web
from app.services.competitors.registries import candidate_generator_registry, web_query_template_registry
from app.services.competitors.sources.base import SourceDeps
from app.services.competitors.sources.brief import category, top_offering
from app.services.competitors.sources.extraction import SearchEntityExtractor

QueryTemplate = Callable[[BusinessProfile], str | None]
B2B_MODELS = frozenset({BusinessModel.b2b_saas, BusinessModel.agency, BusinessModel.manufacturer})


def _country_name(profile: BusinessProfile) -> str | None:
    markets = profile.identity.primary_markets or [code for code in [profile.identity.country] if code]
    return TAVILY_COUNTRY_NAMES.get(markets[0]) if markets else None


web_query_template_registry.add(
    "top_competitors", lambda profile: f"top competitors of {profile.identity.name}"
)
web_query_template_registry.add("alternatives", lambda profile: f"{profile.identity.name} alternatives")
web_query_template_registry.add(
    "local_best",
    lambda profile: (
        f"best {top_offering(profile)} {category(profile)} in {profile.locations.cities[0]}"
        if top_offering(profile) and profile.locations.cities
        else None
    ),
)
web_query_template_registry.add(
    "category_list",
    lambda profile: (
        f"largest {category(profile)} chains in {_country_name(profile)}" if _country_name(profile) else None
    ),
)
web_query_template_registry.add("versus", lambda profile: f"{profile.identity.name} vs")
web_query_template_registry.add(
    "b2b_reviews",
    lambda profile: (
        f"{profile.identity.name} competitors G2" if profile.identity.business_model in B2B_MODELS else None
    ),
)


@candidate_generator_registry.register("web_search")
class WebSearchCandidateSource:
    kind = "web_search"
    used_in_rediscovery = True
    max_queries = 6

    def __init__(self, deps: SourceDeps) -> None:
        self.web_search = deps.web_search
        self.extractor = SearchEntityExtractor(deps)

    def enabled(self, ctx: DiscoveryContext) -> bool:
        return self.web_search is not None and ctx.source_enabled(self.kind)

    def queries(self, ctx: DiscoveryContext) -> list[WebSearchQuery]:
        country = _country_name(ctx.profile)
        exclude = sorted({"youtube.com", *ctx.client_domains})
        texts = [
            text for template in web_query_template_registry.all().values() if (text := template(ctx.profile))
        ]
        return [
            WebSearchQuery(
                query=text,
                max_results=ctx.settings.web_search_max_results,
                exclude_domains=exclude,
                country=country,
                search_depth="basic",
            )
            for text in texts[: self.max_queries]
        ]

    async def generate(self, ctx: DiscoveryContext) -> list[RawCandidate]:
        responses = await asyncio.gather(
            *(search_web(ctx, self.web_search, query) for query in self.queries(ctx))
        )
        hits: list[WebSearchHit] = []
        seen: set[str] = set()
        for response in responses:
            for hit in response.hits if response else []:
                if hit.url not in seen:
                    seen.add(hit.url)
                    hits.append(hit)
        return await self.extractor.extract(ctx, hits, self.kind, ctx.settings.source_caps.web_search)
