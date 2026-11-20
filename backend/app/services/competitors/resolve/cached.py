from app.domain.youtube import OwnershipLabel
from app.services.competitors.context import DiscoveryContext
from app.services.competitors.model import CandidateEntity, LeadRef, LinkEvidence
from app.services.competitors.registries import channel_resolver_registry
from app.services.competitors.resolve.base import ResolverCost, ResolverDeps
from app.services.competitors.resolve.resolution_cache import cache_keys

# site-side evidence cannot be re-observed without crawling, so it is carried over from the cache
SITE_SIDE_SIGNALS = frozenset({"site_links_channel", "jsonld_sameas", "embed_majority"})


@channel_resolver_registry.register("resolution_cache")
class CachedResolutionResolver:
    name = "resolution_cache"
    order = 10
    cost = ResolverCost()

    def __init__(self, deps: ResolverDeps) -> None:
        self.cache = deps.cache

    def applicable(self, entity: CandidateEntity, ctx: DiscoveryContext) -> bool:
        return self.cache is not None and not entity.channel_hints

    async def resolve(self, entity: CandidateEntity, ctx: DiscoveryContext) -> list[LeadRef]:
        hits = await self.cache.lookup(cache_keys(entity, ctx.market), ctx.now)
        refs = []
        for hit in hits:
            if hit.label not in {OwnershipLabel.verified, OwnershipLabel.likely}:
                continue
            evidence = [
                LinkEvidence(name=signal.name, detail=signal.detail, source_url=signal.source_url)
                for signal in hit.signals
                if signal.name in SITE_SIDE_SIGNALS
            ]
            refs.append(
                LeadRef(kind="channel_id", value=hit.channel_id, method="resolution_cache", evidence=evidence)
            )
        return refs


@channel_resolver_registry.register("known_channels")
class KnownChannelResolver:
    """channels already in hand: user links and niche-search hits; costs nothing beyond evidence."""

    name = "known_channels"
    order = 0
    cost = ResolverCost()

    def __init__(self, deps: ResolverDeps) -> None:
        pass

    def applicable(self, entity: CandidateEntity, ctx: DiscoveryContext) -> bool:
        return bool(entity.channel_hints or entity.lead_refs)

    async def resolve(self, entity: CandidateEntity, ctx: DiscoveryContext) -> list[LeadRef]:
        refs = list(entity.lead_refs)
        claimed = {ref.value for ref in refs}
        refs.extend(
            LeadRef(kind="channel_id", value=channel_id, method="search_list")
            for channel_id in entity.channel_hints
            if channel_id not in claimed
        )
        return refs
