import asyncio
from dataclasses import dataclass
from uuid import UUID, uuid5

from app.core.config import CompetitorSettings
from app.domain.competitors import (
    CandidateRole,
    CandidateStatus,
    ChannelEvidence,
    ChannelMatch,
    CompetitorCandidate,
    CompetitorRelation,
)
from app.domain.runs import CompetitorCandidatePayload
from app.domain.youtube import OwnershipLabel
from app.pipeline.contracts import BudgetExceeded
from app.ports.crawler import SiteCrawler
from app.ports.llm import LLMClient
from app.ports.progress import ProgressReporter
from app.ports.quota import QuotaExhausted
from app.ports.search import WebSearch
from app.ports.youtube import YouTubeGateway
from app.services.competitors.classify.relation_classifier import RelationClassifier
from app.services.competitors.context import DiscoveryContext
from app.services.competitors.identity.hallucination_guard import HallucinationGuard
from app.services.competitors.identity.merger import EntityMerger, suppress_rejected
from app.services.competitors.identity.self_filter import SelfIdentityFilter
from app.services.competitors.model import (
    CandidateEntity,
    ChannelLead,
    DiscoveryResult,
    RawCandidate,
)
from app.services.competitors.registries import candidate_generator_registry
from app.services.competitors.resolve.base import ResolverDeps
from app.services.competitors.resolve.chain import ResolutionChain
from app.services.competitors.resolve.resolution_cache import BrandChannelCache, CachedResolution, cache_keys
from app.services.competitors.score.business import BusinessRelevanceScorer
from app.services.competitors.score.confidence import candidate_confidence
from app.services.competitors.score.topics import PhraseTopicMatcher
from app.services.competitors.score.youtube import YouTubeRelevanceScorer
from app.services.competitors.select.assessment import TIER_RANK, AssessedCandidate, default_role
from app.services.competitors.select.auto_confirm import AutoConfirmPolicy
from app.services.competitors.select.proposer import SetProposer
from app.services.competitors.sources.base import CandidateGenerator, SourceDeps
from app.services.competitors.summaries import EvidenceSummaryWriter
from app.services.competitors.verify.adjudicator import LlmOwnershipAdjudicator
from app.services.competitors.verify.grouping import MultiChannelGrouper
from app.services.competitors.verify.verifier import OwnershipVerifier
from app.services.competitors.youtube_access import MeteredYouTube

CANDIDATE_NAMESPACE = UUID("5f0c6a52-7f53-4d4b-9a59-6f2d0c4e0a04")
SOURCE_GROUP = {
    "user": 1,
    "website_hint": 2,
    "web_search": 3,
    "rediscovery_news": 3,
    "niche_search": 4,
    "llm": 5,
}
RELATION_ORDER = {
    CompetitorRelation.direct: 0,
    CompetitorRelation.direct_public: 1,
    CompetitorRelation.aggregator: 2,
    CompetitorRelation.benchmark: 3,
    CompetitorRelation.content: 4,
}
PAYLOAD_SOURCE = {
    "web_search": "web_search",
    "rediscovery_news": "web_search",
    "niche_search": "youtube_search",
    "website_hint": "profile",
    "llm": "llm",
    "user": "user",
}


@dataclass(frozen=True)
class DiscoveryDeps:
    youtube: YouTubeGateway
    web_search: WebSearch | None = None
    crawler: SiteCrawler | None = None
    llm: LLMClient | None = None
    cache: BrandChannelCache | None = None
    reporter: ProgressReporter | None = None


def resolution_priority(entity: CandidateEntity, business_tier: str) -> tuple:
    # multi-source first, then user, website hint, web search, niche search, llm-only; ties go to the web tier
    kinds = entity.source_kinds
    group = (
        0
        if len(kinds) > 1 or "carried_over" in entity.flags
        else min(SOURCE_GROUP.get(kind, 6) for kind in kinds)
    )
    return (
        group,
        RELATION_ORDER.get(entity.relation, 5),
        TIER_RANK[business_tier],
        -len(entity.sources),
        -entity.query_hits,
        entity.normalized_name,
    )


def candidate_id(client_id: UUID, entity: CandidateEntity) -> UUID:
    if entity.known is not None:
        return entity.known.id
    return uuid5(CANDIDATE_NAMESPACE, f"{client_id}:{entity.official_domain or entity.normalized_name}")


class CompetitorDiscoveryService:
    """one discovery run: sources, identity, classification, resolution, verification, scoring, proposal."""

    def __init__(self, deps: DiscoveryDeps, settings: CompetitorSettings) -> None:
        self.deps = deps
        self.settings = settings

    async def discover(self, ctx: DiscoveryContext) -> DiscoveryResult:
        youtube = MeteredYouTube(self.deps.youtube, ctx)
        raws = await self._generate(SourceDeps(youtube, self.deps.web_search, self.deps.llm), ctx)
        kinds = {raw.source.kind for raw in raws}
        await self._step(
            "competitors.sources", f"Found {len(raws)} possible competitors from {len(kinds)} sources"
        )

        merger = EntityMerger(self.settings.merge_name_ratio)
        entities = merger.merge(raws, ctx.known)
        group = SelfIdentityFilter(ctx.profile, ctx.own_channel_ids, ctx.client_domains).apply(entities)
        suppress_rejected(entities)
        await HallucinationGuard(self.deps.web_search).check(entities, ctx)
        await RelationClassifier(self.deps.llm).classify(entities, ctx)

        business_scorer = BusinessRelevanceScorer(ctx.profile, self.settings)
        business = {entity.key: business_scorer.score(entity) for entity in entities}
        for entity in entities:
            if business[entity.key].tier == "low":
                entity.flags.add("business_tier_low")

        active = sorted(
            (entity for entity in entities if not entity.is_excluded),
            key=lambda entity: resolution_priority(entity, business[entity.key].tier),
        )
        to_resolve = active[: self.settings.max_entities_to_resolve]
        for entity in active[self.settings.max_entities_to_resolve :]:
            entity.status = CandidateStatus.unresolved
        if len(active) > len(to_resolve):
            ctx.warn(
                "CANDIDATES_UNCHECKED",
                f"{len(active) - len(to_resolve)} more possible competitors not checked yet.",
            )

        verifier = OwnershipVerifier(self.settings)
        adjudicator = LlmOwnershipAdjudicator(self.deps.llm, self.settings)
        chain = ResolutionChain(
            ResolverDeps(youtube, self.deps.web_search, self.deps.crawler, self.deps.cache),
            self.settings,
            verifier,
            adjudicator,
        )
        await chain.resolve_all(to_resolve, ctx)
        entities = merger.merge_by_channel(entities)
        resolved = [entity for entity in entities if entity.is_resolved(verified_only=False)]
        verified = [entity for entity in entities if entity.is_resolved()]
        await self._step(
            "competitors.resolve", f"Matched {len(resolved)} to YouTube channels ({len(verified)} verified)"
        )

        assessed = await self._assess(entities, business, youtube, ctx)
        proposal = SetProposer(self.settings).propose(ctx.client_id, assessed)
        proposal = proposal.model_copy(
            update={
                "auto_confirmable": AutoConfirmPolicy(self.settings).allows(proposal, assessed, ctx.degraded)
            }
        )
        if not proposal.ready:
            found = len(proposal.competitor_channel_ids)
            ctx.warn(
                "TOO_FEW_COMPETITORS",
                f"We found {found} so far. Add at least {self.settings.min_competitors - found} more to continue.",
            )
        await self._step(
            "competitors.propose", f"Proposed {len(proposal.competitor_channel_ids)} competitors"
        )

        summaries = await EvidenceSummaryWriter(self.deps.llm).write(
            [item for item in assessed if not item.entity.is_excluded], ctx
        )
        candidates = [self._candidate(item, summaries.get(item.entity.key, ""), ctx) for item in assessed]
        await self._store_resolutions(assessed, ctx)
        for item, candidate in zip(assessed, candidates, strict=True):
            if candidate.role is not CandidateRole.excluded:
                await self._insight(candidate, item)
        return DiscoveryResult(
            discovery_run_id=ctx.discovery_run_id,
            candidates=candidates,
            proposal=proposal,
            own_channel_suggestions=sorted(
                {
                    channel_id
                    for entity in group
                    for channel_id in entity.channel_hints
                    if entity.relation is not CompetitorRelation.self_
                }
            ),
            warnings=list(ctx.warnings),
            usage=ctx.meter.usage(),
            partial=any(
                warning.code in {"DISCOVERY_TIME_LIMIT", "RESOLUTION_PARTIAL"} for warning in ctx.warnings
            ),
        )

    async def _generate(self, deps: SourceDeps, ctx: DiscoveryContext) -> list[RawCandidate]:
        generators: list[CandidateGenerator] = [
            factory(deps) for factory in candidate_generator_registry.all().values()
        ]
        enabled = [generator for generator in generators if generator.enabled(ctx)]
        results = await asyncio.gather(*(self._safe_generate(generator, ctx) for generator in enabled))
        return [raw for batch in results for raw in batch]

    @staticmethod
    async def _safe_generate(generator: CandidateGenerator, ctx: DiscoveryContext) -> list[RawCandidate]:
        try:
            return await generator.generate(ctx)
        except (BudgetExceeded, QuotaExhausted):
            ctx.warn(
                "SOURCE_SKIPPED",
                f"The {generator.kind.replace('_', ' ')} source was skipped to stay within limits.",
            )
            return []

    async def _assess(
        self,
        entities: list[CandidateEntity],
        business: dict,
        youtube: MeteredYouTube,
        ctx: DiscoveryContext,
    ) -> list[AssessedCandidate]:
        matcher = (
            PhraseTopicMatcher.from_topic_refs(ctx.topic_refs)
            if ctx.topic_refs
            else PhraseTopicMatcher.from_profile(ctx.profile)
        )
        scorer = YouTubeRelevanceScorer(matcher, self.settings, await self._client_subscribers(youtube, ctx))
        grouper = MultiChannelGrouper(ctx.profile)
        thin = ctx.profile.quality.label == "thin"
        assessed = []
        for entity in entities:
            item = AssessedCandidate(entity=entity, business=business[entity.key])
            for lead in entity.leads.values():
                assessment = scorer.assess(lead, ctx.now)
                if assessment is not None:
                    item.assessments[lead.channel_id] = assessment
                    self._flag_activity(lead, assessment.evidence)
            matching = {
                channel_id: assessment.matching_uploads for channel_id, assessment in item.assessments.items()
            }
            item.primary = grouper.group(entity, matching) if not entity.is_excluded else None
            item.confidence = candidate_confidence(entity, item.business, item.primary, thin)
            if thin and item.confidence == "high":
                item.confidence = "medium"
            item.role = (
                entity.intended_role
                if entity.intended_role and not entity.is_excluded
                else default_role(entity)
            )
            assessed.append(item)
        return assessed

    @staticmethod
    def _flag_activity(lead: ChannelLead, evidence: ChannelEvidence) -> None:
        if evidence.uploads_90d == 0:
            lead.flags.add("inactive")
        if evidence.video_count < 5:
            lead.flags.add("small_sample")
        if evidence.subscriber_count is None:
            lead.flags.add("hidden_subs")

    async def _client_subscribers(self, youtube: MeteredYouTube, ctx: DiscoveryContext) -> int | None:
        if not ctx.own_channel_ids:
            return None
        try:
            records = await youtube.channels(ctx.own_channel_ids[:1])
        except (BudgetExceeded, QuotaExhausted):
            return None
        record = records.get(ctx.own_channel_ids[0])
        return None if record is None or record.hidden_subscriber_count else record.subscriber_count

    def _candidate(self, item: AssessedCandidate, summary: str, ctx: DiscoveryContext) -> CompetitorCandidate:
        entity = item.entity
        status = self._status(item)
        channels = []
        for lead in sorted(entity.leads.values(), key=lambda lead: -lead.score):
            assessment = item.assessments.get(lead.channel_id)
            if assessment is None or lead.verdict is None:
                continue
            channels.append(
                ChannelMatch(
                    channel_id=lead.channel_id,
                    handle=lead.record.handle,
                    title=lead.record.title,
                    resolution_method=lead.method,
                    variant=lead.variant,
                    is_primary=item.primary is not None and lead.channel_id == item.primary.channel_id,
                    ownership=lead.verdict,
                    youtube=assessment.evidence,
                    youtube_relevance=assessment.relevance,
                    flags=sorted(lead.flags),
                )
            )
        known = entity.known
        return CompetitorCandidate(
            id=candidate_id(ctx.client_id, entity),
            client_id=ctx.client_id,
            brand_name=entity.brand_name,
            normalized_name=entity.normalized_name,
            aliases=sorted(entity.aliases),
            official_domain=entity.official_domain if "domain_unverified" not in entity.flags else None,
            relation=entity.relation or CompetitorRelation.unrelated,
            owner_kind=entity.owner_kind,
            role=CandidateRole.excluded
            if status in {CandidateStatus.excluded, CandidateStatus.rejected}
            else item.role,
            geography=entity.geography,
            sources=entity.sources,
            business=item.business,
            channels=channels,
            confidence=item.confidence,
            status=status,
            flags=sorted(entity.flags),
            evidence_summary=summary,
            decided_by=known.decided_by if known is not None else ("system" if entity.is_excluded else None),
            rejection_reason=entity.rejection_reason,
            first_seen_run_id=(
                known.first_seen_run_id if known and known.first_seen_run_id else ctx.discovery_run_id
            ),
            last_seen_run_id=ctx.discovery_run_id,
        )

    @staticmethod
    def _status(item: AssessedCandidate) -> CandidateStatus:
        entity = item.entity
        if entity.status in {CandidateStatus.excluded, CandidateStatus.rejected, CandidateStatus.unresolved}:
            return entity.status
        known = entity.known
        if known is not None and known.decided_by == "user":
            return known.status
        if known is not None and known.status is CandidateStatus.auto_confirmed:
            return known.status
        if item.primary is None:
            return CandidateStatus.no_channel if entity.resolution_attempted else CandidateStatus.unresolved
        return CandidateStatus.user_added if entity.is_user else CandidateStatus.suggested

    async def _store_resolutions(self, assessed: list[AssessedCandidate], ctx: DiscoveryContext) -> None:
        # only public brand-to-channel facts enter the cross-client cache
        if self.deps.cache is None:
            return
        entries = []
        for item in assessed:
            primary = item.primary
            if (
                item.entity.is_excluded
                or primary is None
                or primary.label not in {OwnershipLabel.verified, OwnershipLabel.likely}
            ):
                continue
            if item.entity.is_user and not primary.has_link_signal:
                continue
            entries.extend(
                CachedResolution(
                    key_type=key_type,
                    key=key,
                    channel_id=primary.channel_id,
                    label=primary.label,
                    score=primary.score,
                    method=primary.verdict.method,
                    signals=[signal for signal in primary.verdict.signals if signal.name != "user_asserted"],
                    verified_at=ctx.now,
                )
                for key_type, key in cache_keys(item.entity, ctx.market)
            )
        if entries:
            await self.deps.cache.store(entries, self.settings.resolution_cache_days)

    async def _step(self, step: str, message: str) -> None:
        if self.deps.reporter is not None:
            await self.deps.reporter.step(step, message)

    async def _insight(self, candidate: CompetitorCandidate, item: AssessedCandidate) -> None:
        if self.deps.reporter is None:
            return
        primary = next((channel for channel in candidate.channels if channel.is_primary), None)
        record = item.primary.record if item.primary is not None else None
        kinds = [source.kind for source in candidate.sources]
        payload = CompetitorCandidatePayload(
            candidate_id=candidate.id,
            name=candidate.brand_name,
            channel_id=primary.channel_id if primary else None,
            thumbnail_url=record.thumbnail_url if record is not None else None,
            source=PAYLOAD_SOURCE.get(kinds[0], "web_search") if kinds else "profile",
            evidence_count=len(candidate.sources) + (len(primary.ownership.signals) if primary else 0),
        )
        await self.deps.reporter.insight(f"Found {candidate.brand_name}", payload)
