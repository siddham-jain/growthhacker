from app.ports.search import WebSearchQuery
from app.services.competitors.context import DiscoveryContext
from app.services.competitors.model import CandidateEntity, LeadRef, LinkEvidence
from app.services.competitors.providers import search_web
from app.services.competitors.registries import channel_resolver_registry
from app.services.competitors.resolve.base import ResolverCost, ResolverDeps, has_likely_lead
from app.services.competitors.resolve.youtube_url import YouTubeRef, parse_youtube_url


@channel_resolver_registry.register("websearch_youtube")
class WebSearchYouTubeResolver:
    """asks the web index for the brand's channel; results are only parsed as urls, never fetched."""

    name = "websearch_youtube"
    order = 30
    cost = ResolverCost(tavily_credits=1)

    def __init__(self, deps: ResolverDeps) -> None:
        self.web_search = deps.web_search

    def applicable(self, entity: CandidateEntity, ctx: DiscoveryContext) -> bool:
        return self.web_search is not None and not entity.channel_hints and not has_likely_lead(entity, ctx)

    async def resolve(self, entity: CandidateEntity, ctx: DiscoveryContext) -> list[LeadRef]:
        query = WebSearchQuery(
            query=f"{entity.brand_name} official YouTube channel",
            include_domains=["youtube.com"],
            max_results=5,
            search_depth="basic",
        )
        response = await search_web(ctx, self.web_search, query)
        if response is None:
            return []
        refs = []
        for rank, hit in enumerate(response.hits, start=1):
            parsed = parse_youtube_url(hit.url)
            if not isinstance(parsed, YouTubeRef):
                continue
            evidence = LinkEvidence(
                name="websearch_youtube_hit",
                detail=f'web result #{rank} for "{query.query}" points to {hit.url}',
                source_url=hit.url,
                rank=rank,
            )
            refs.append(
                LeadRef(kind=parsed.kind, value=parsed.value, method="websearch_youtube", evidence=[evidence])
            )
        return refs
