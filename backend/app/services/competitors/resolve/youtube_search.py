from app.domain.youtube import SearchQuery
from app.services.competitors.context import DiscoveryContext
from app.services.competitors.model import DIRECT_RELATIONS, CandidateEntity, LeadRef
from app.services.competitors.registries import channel_resolver_registry
from app.services.competitors.resolve.base import ResolverCost, ResolverDeps, has_likely_lead

MAX_HITS = 3


@channel_resolver_registry.register("search_list")
class YouTubeSearchResolver:
    """last resort: one search.list call from the search bucket, capped per run."""

    name = "search_list"
    order = 50
    cost = ResolverCost(search_calls=1)

    def __init__(self, deps: ResolverDeps) -> None:
        self.youtube = deps.youtube

    def applicable(self, entity: CandidateEntity, ctx: DiscoveryContext) -> bool:
        if entity.channel_hints or has_likely_lead(entity, ctx):
            return False
        if ctx.meter.resolution_searches >= ctx.meter.max_resolution_searches:
            return False
        eligible_kind = entity.relation in DIRECT_RELATIONS or entity.is_user
        return eligible_kind and "business_tier_low" not in entity.flags

    async def resolve(self, entity: CandidateEntity, ctx: DiscoveryContext) -> list[LeadRef]:
        query = SearchQuery(q=entity.brand_name, type="channel", region_code=ctx.market, max_results=5)
        page = await self.youtube.search(query, "resolution")
        channel_ids = [hit.channel_id for hit in page.items if hit.kind == "channel"][:MAX_HITS]
        return [
            LeadRef(kind="channel_id", value=channel_id, method="search_list", guessed=True)
            for channel_id in channel_ids
        ]
