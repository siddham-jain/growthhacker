import asyncio

from app.domain.competitors import CandidateSource, CandidateStatus
from app.ports.search import WebSearch, WebSearchHit, WebSearchQuery
from app.services.competitors.context import DiscoveryContext
from app.services.competitors.identity.normalize import normalize_name, normalize_text, registrable_domain
from app.services.competitors.model import CandidateEntity
from app.services.competitors.providers import search_web


def client_context_terms(ctx: DiscoveryContext) -> set[str]:
    profile = ctx.profile
    terms = {keyword.term for keyword in profile.niche_keywords}
    terms.update(profile.locations.cities)
    terms.update(profile.locations.regions)
    terms.update(offering.name for offering in profile.offerings)
    terms.add(profile.identity.niche)
    return {normalize_text(term) for term in terms if term and len(term) > 2}


class HallucinationGuard:
    """an entity named only by the llm must be corroborated by an independent web result."""

    def __init__(self, web_search: WebSearch | None) -> None:
        self.web_search = web_search

    async def check(self, entities: list[CandidateEntity], ctx: DiscoveryContext) -> None:
        suspects = [entity for entity in entities if "llm_only" in entity.flags and not entity.is_excluded]
        if not suspects:
            return
        if self.web_search is None or "web_search" in ctx.degraded:
            for entity in suspects:
                entity.flags.add("uncorroborated")
            return
        # at most half of the remaining web budget, so resolution still has credits left
        remaining = ctx.settings.budget.tavily_credits - ctx.meter.tavily_credits
        cap = min(ctx.settings.hallucination_guard_searches, int(remaining // 2))
        checked, deferred = suspects[:cap], suspects[cap:]
        for entity in deferred:
            entity.flags.add("uncorroborated")
        terms = client_context_terms(ctx)
        results = await asyncio.gather(*(self._probe(entity, ctx) for entity in checked))
        for entity, hits in zip(checked, results, strict=True):
            if hits is None:
                entity.flags.add("uncorroborated")
                continue
            corroboration = self._corroborating_hit(entity, hits, terms)
            if corroboration is None:
                entity.status = CandidateStatus.excluded
                entity.rejection_reason = "no_independent_evidence"
                entity.flags.add("hallucination_dropped")
                continue
            hit, by_domain = corroboration
            entity.flags.discard("llm_only")
            entity.flags.add("guard_corroborated")
            if by_domain:
                entity.flags.discard("domain_unverified")
            entity.sources.append(
                CandidateSource(
                    kind="web_search", ref=hit.url, snippet=hit.snippet[:500], retrieved_at=ctx.now
                )
            )

    async def _probe(self, entity: CandidateEntity, ctx: DiscoveryContext) -> list[WebSearchHit] | None:
        query = WebSearchQuery(query=f'"{entity.brand_name}"', max_results=5, search_depth="basic")
        response = await search_web(ctx, self.web_search, query)
        return response.hits if response is not None else None

    @staticmethod
    def _corroborating_hit(
        entity: CandidateEntity, hits: list[WebSearchHit], terms: set[str]
    ) -> tuple[WebSearchHit, bool] | None:
        name = normalize_name(entity.brand_name)
        for hit in hits:
            if entity.official_domain and registrable_domain(hit.url) == entity.official_domain:
                return hit, True
        for hit in hits:
            text = f" {normalize_text(hit.title + ' ' + hit.snippet)} "
            if f" {name} " in text and any(f" {term} " in text for term in terms):
                return hit, False
        return None
