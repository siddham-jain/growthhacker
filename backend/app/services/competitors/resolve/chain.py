import asyncio

from app.core.config import CompetitorSettings
from app.domain.competitors import CompetitorRelation
from app.pipeline.contracts import BudgetExceeded
from app.ports.quota import QuotaExhausted
from app.services.competitors.context import DiscoveryContext
from app.services.competitors.model import CandidateEntity, ChannelLead, LeadRef
from app.services.competitors.registries import channel_resolver_registry
from app.services.competitors.resolve.base import ChannelResolver, ResolverDeps
from app.services.competitors.resolve.evidence import EvidenceCollector
from app.services.competitors.resolve.materialise import LeadMaterialiser, lead_priority
from app.services.competitors.verify.adjudicator import LlmOwnershipAdjudicator
from app.services.competitors.verify.verifier import OwnershipVerifier

UPLOAD_PRIORITY = {
    CompetitorRelation.direct: 1,
    CompetitorRelation.direct_public: 2,
    CompetitorRelation.aggregator: 3,
    CompetitorRelation.benchmark: 4,
    CompetitorRelation.content: 5,
}


class ResolutionChain:
    """runs resolvers cheapest first across all entities; an entity leaves the chain once a lead is verified."""

    def __init__(
        self,
        deps: ResolverDeps,
        settings: CompetitorSettings,
        verifier: OwnershipVerifier,
        adjudicator: LlmOwnershipAdjudicator,
    ) -> None:
        self.settings = settings
        # cheapest first by declared cost (search calls, then units, then credits); order breaks ties
        self.resolvers: list[ChannelResolver] = sorted(
            (factory(deps) for factory in channel_resolver_registry.all().values()),
            key=lambda resolver: (*resolver.cost.rank, resolver.order),
        )
        self.materialiser = LeadMaterialiser(deps.youtube, settings)
        self.collector = EvidenceCollector(deps.youtube, settings)
        self.verifier = verifier
        self.adjudicator = adjudicator

    async def resolve_all(self, entities: list[CandidateEntity], ctx: DiscoveryContext) -> None:
        pending = list(entities)
        semaphore = asyncio.Semaphore(self.settings.resolve_concurrency)
        for resolver in self.resolvers:
            if ctx.meter.out_of_time():
                ctx.warn(
                    "DISCOVERY_TIME_LIMIT",
                    f"Still checking {len(pending)} competitors; we'll update the list.",
                )
                break
            batch = [entity for entity in pending if resolver.applicable(entity, ctx)]
            if not batch:
                continue
            refs = await asyncio.gather(*(self._run(resolver, entity, ctx, semaphore) for entity in batch))
            exhausted = await self._absorb(list(zip(batch, refs, strict=True)), ctx)
            pending = [entity for entity in pending if not entity.is_resolved()]
            if exhausted:
                break
        for entity in entities:
            entity.resolution_attempted = True
        await self._adjudicate(entities, ctx)

    async def _run(
        self,
        resolver: ChannelResolver,
        entity: CandidateEntity,
        ctx: DiscoveryContext,
        semaphore: asyncio.Semaphore,
    ) -> list[LeadRef]:
        async with semaphore:
            try:
                return await resolver.resolve(entity, ctx)
            except BudgetExceeded:
                return []
            except QuotaExhausted:
                ctx.warn(
                    "YOUTUBE_QUOTA_LOW", "Partial competitor scan. More suggestions will appear tomorrow."
                )
                return []

    async def _absorb(
        self, found: list[tuple[CandidateEntity, list[LeadRef]]], ctx: DiscoveryContext
    ) -> bool:
        touched = [entity for entity, refs in found if refs or entity.leads]
        exhausted = False
        try:
            await self.materialiser.apply([(entity, refs) for entity, refs in found if refs])
            await self.collector.channels(touched)
        except (BudgetExceeded, QuotaExhausted):
            ctx.warn("RESOLUTION_PARTIAL", "Partial competitor scan. More suggestions will appear tomorrow.")
            exhausted = True
        for entity in touched:
            for channel_id in [cid for cid, lead in entity.leads.items() if lead.record is None]:
                del entity.leads[channel_id]
            self.verifier.verify_entity(entity)
        await self.collector.uploads(self._upload_candidates(touched), ctx)
        for entity in touched:
            self.verifier.verify_entity(entity)
        return exhausted

    def _upload_candidates(self, entities: list[CandidateEntity]) -> list[ChannelLead]:
        # a found channel with only a strong name match is still worth an uploads check: the uploads are
        # where its link and brand evidence lives, so a uncertain_min floor would never let it earn any
        weights = self.settings.ownership
        floor = min(weights.uncertain_min, weights.name_similarity_strong)
        ordered = sorted(
            entities,
            key=lambda entity: (
                not entity.is_user,
                "business_tier_low" in entity.flags,
                UPLOAD_PRIORITY.get(entity.relation, 6),
                -entity.query_hits,
            ),
        )
        leads = []
        for entity in ordered:
            ranked = sorted(entity.leads.values(), key=lead_priority)
            leads.extend(lead for lead in ranked[: self.settings.max_leads_per_entity] if lead.score >= floor)
        return leads

    async def _adjudicate(self, entities: list[CandidateEntity], ctx: DiscoveryContext) -> None:
        reviews = []
        for entity in entities:
            if entity.is_resolved(verified_only=False):
                continue
            lead = entity.best_lead()
            if lead is not None and self.adjudicator.needs_review(lead):
                reviews.append(self.adjudicator.adjudicate(entity, lead, ctx))
        semaphore = asyncio.Semaphore(self.settings.llm_concurrency)

        async def bounded(review):
            async with semaphore:
                await review

        await asyncio.gather(*(bounded(review) for review in reviews))
