import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID

import numpy as np
from numpy.typing import NDArray

from app.core.config import NewsSettings
from app.domain.news import (
    ConfirmationStatus,
    DemandCheck,
    NewsItem,
    NewsJudgement,
    NewsSignal,
    RelevanceBand,
    SourceTier,
)
from app.domain.recommendations import GateAction, GateResult, RecommendationDraft
from app.services.news.context import NewsClientContext
from app.services.news.credibility import CredibilityScorer, corroborated
from app.services.news.demand import DemandOutcome, DemandRequest, YouTubeDemandChecker
from app.services.news.gates import NewsEvidenceGate
from app.services.news.grounding import GroundingValidator
from app.services.news.history import NewsReactiveHistory, PastNewsEntity
from app.services.news.judge import NewsRelevanceJudge
from app.services.news.lifecycle import LifecycleChange, SignalLifecycle, SignalWithdrawal
from app.services.news.opportunity import NewsOpportunityBuilder, OpportunityInputs, reference_sources
from app.services.news.packs import SourcePack
from app.services.news.prefilter import PrefilteredStory, RelevancePrefilter, StoryBundle
from app.services.news.ranking import (
    LONG_LIVED_EVENTS,
    RankedCandidate,
    decay_for,
    entity_family,
    expires_at,
    flood_control,
    freshness,
    relevance_score,
    urgency,
)
from app.services.news.safety import SafetyInput, SafetyScreen
from app.services.news.text import contains_phrase
from app.services.news.tiers import TIER_RANK

SIGNAL_NAMESPACE = uuid.UUID("9a4d1f3b-7e2c-4c8a-b6f1-0d2e3c4b5a69")
EMIT_DECISIONS = frozenset({"allow", "allow_with_guidance", "needs_review"})
REFRESH_BANDS = frozenset({RelevanceBand.strong, RelevanceBand.direct})


def signal_id_for(client_id: UUID, story_id: UUID) -> UUID:
    return uuid.uuid5(SIGNAL_NAMESPACE, f"{client_id}:{story_id}")


@dataclass(frozen=True)
class StoredJudgement:
    story_id: UUID
    profile_version: int
    judgement: NewsJudgement
    aliases: list[str]
    refresh_keywords: list[str]
    outlet_count: int
    best_tier: SourceTier
    model: str
    tokens_in: int = 0
    tokens_out: int = 0


@dataclass(frozen=True)
class ScanInputs:
    ctx: NewsClientContext
    bundles: Sequence[StoryBundle]
    now: datetime
    run_id: UUID | None = None
    recent_items: Sequence[NewsItem] = ()
    existing_signals: Sequence[NewsSignal] = ()
    judgements: Mapping[UUID, StoredJudgement] = field(default_factory=dict)
    past_entities: Sequence[PastNewsEntity] = ()
    centroids: Mapping[UUID, NDArray[np.float32]] = field(default_factory=dict)
    business_vector: NDArray[np.float32] | None = None


@dataclass
class ScanOutcome:
    signals: list[NewsSignal] = field(default_factory=list)
    new_judgements: list[StoredJudgement] = field(default_factory=list)
    drafts: list[RecommendationDraft] = field(default_factory=list)
    gate_results: list[GateResult] = field(default_factory=list)
    gate_suppressed: list[tuple[RecommendationDraft, GateResult]] = field(default_factory=list)
    review: list[tuple[UUID, str]] = field(default_factory=list)
    withdrawals: list[SignalWithdrawal] = field(default_factory=list)
    changes: list[LifecycleChange] = field(default_factory=list)
    suppressed_stories: dict[str, int] = field(default_factory=dict)
    demand_videos: dict[UUID, DemandOutcome] = field(default_factory=dict)

    def count(self, reason: str) -> None:
        self.suppressed_stories[reason] = self.suppressed_stories.get(reason, 0) + 1


@dataclass
class _Evaluated:
    signal: NewsSignal
    bundle: StoryBundle
    demand: DemandOutcome | None
    emit: bool
    fresh: float


class NewsScanner:
    """one client, one run: judge -> ground -> credibility -> safety -> demand -> signals -> drafts."""

    def __init__(
        self,
        *,
        settings: NewsSettings,
        packs: Sequence[SourcePack],
        judge: NewsRelevanceJudge,
        demand: YouTubeDemandChecker,
        history: NewsReactiveHistory,
        safety: SafetyScreen | None = None,
    ) -> None:
        self._settings = settings
        self._packs = list(packs)
        self._judge = judge
        self._demand = demand
        self._history = history
        self._safety = safety or SafetyScreen()
        self._prefilter = RelevancePrefilter(settings)
        self._credibility = CredibilityScorer(settings.credibility_min)
        self._grounding = GroundingValidator([pair for p in self._packs for pair in p.alias_pairs])
        self._lifecycle = SignalLifecycle()
        self._builder = NewsOpportunityBuilder(settings)
        self._gate = NewsEvidenceGate(settings)
        self._half_lives = {k: v for pack in self._packs for k, v in pack.half_life_hours.items()}
        self._ambiguous = [term for pack in self._packs for term in pack.ambiguous_terms]
        self._alarm_words = [word for pack in self._packs for word in pack.alarm_words]
        self._demo_events = frozenset(e for pack in self._packs for e in pack.demo_event_types)

    def _needs_rejudge(self, stored: StoredJudgement, bundle: StoryBundle, ctx: NewsClientContext) -> bool:
        if stored.profile_version != ctx.profile_version:
            return True
        better_tier = TIER_RANK[bundle.story.best_tier] > TIER_RANK[stored.best_tier]
        new_outlets = bundle.story.outlet_count - stored.outlet_count
        return better_tier or new_outlets >= self._settings.rejudge_min_new_outlets

    async def _judgement(
        self, candidate: PrefilteredStory, inputs: ScanInputs, outcome: ScanOutcome
    ) -> StoredJudgement | None:
        bundle, ctx = candidate.bundle, inputs.ctx
        stored = inputs.judgements.get(bundle.story.id)
        if stored is not None and not self._needs_rejudge(stored, bundle, ctx):
            return stored
        judged = await self._judge.judge(
            ctx, bundle.story.id, bundle.items, now=inputs.now, run_id=inputs.run_id
        )
        if judged.output is None:
            outcome.count(judged.suppression or "llm_unavailable")
            return None
        grounded = self._grounding.validate(judged.output, judged.aliases, ctx.freshness.watch_entities)
        if grounded.judgement is None:
            outcome.count(grounded.reason or "ungrounded_entity")
            return None
        fresh = StoredJudgement(
            story_id=bundle.story.id,
            profile_version=ctx.profile_version,
            judgement=grounded.judgement,
            aliases=grounded.aliases,
            refresh_keywords=grounded.refresh_keywords,
            outlet_count=bundle.story.outlet_count,
            best_tier=bundle.story.best_tier,
            model=judged.model or "unknown",
            tokens_in=judged.tokens_in,
            tokens_out=judged.tokens_out,
        )
        outcome.new_judgements.append(fresh)
        return fresh

    def _recency_days(self, judgement: NewsJudgement, ctx: NewsClientContext) -> int:
        days = ctx.envelope(self._settings).recency_days
        if judgement.event_type in LONG_LIVED_EVENTS:
            days = max(days, self._settings.long_lived_event_recency_days)
        return days

    def _disambiguator(self, judgement: NewsJudgement) -> str | None:
        family = entity_family(judgement.key_entity.canonical)
        if not any(contains_phrase(family, term) for term in self._ambiguous) or len(family.split()) > 1:
            return None
        org = next((e for e in judgement.other_entities if e.kind == "org"), None)
        return org.text if org else None

    def _already_covered(
        self, ctx: NewsClientContext, signal_entity: str, aliases: Sequence[str], since: datetime
    ) -> bool:
        terms = [signal_entity, *aliases]
        return any(
            v.published_at >= since and any(contains_phrase(f"{v.title}\n{v.description}", t) for t in terms)
            for v in ctx.own_videos
        )

    async def _demand_for(
        self,
        ctx: NewsClientContext,
        stored: StoredJudgement,
        bundle: StoryBundle,
        inputs: ScanInputs,
    ) -> DemandOutcome:
        request = DemandRequest(
            client_id=ctx.client_id,
            run_id=inputs.run_id,
            canonical=stored.judgement.key_entity.canonical,
            aliases=stored.aliases,
            disambiguator=self._disambiguator(stored.judgement),
            first_published_at=bundle.story.first_published_at,
            region=ctx.regions[0] if ctx.regions else None,
            language=ctx.languages[0],
            client_cap=ctx.envelope(self._settings).demand_checks_per_client_per_day,
            own_subscriber_count=ctx.own_subscriber_count,
        )
        return await self._demand.check(request, inputs.now)

    def _unchecked_demand(self, stored: StoredJudgement, bundle: StoryBundle, now: datetime) -> DemandCheck:
        return DemandCheck(
            status="unchecked_disabled",
            entity_query=stored.judgement.key_entity.canonical,
            published_after=bundle.story.first_published_at,
            checked_at=now,
            early_video_count=0,
            channel_count=0,
            outlier_count=0,
            median_views_per_hour=None,
            saturation="empty",
            quota_units_spent=0,
        )

    async def _evaluate(
        self,
        candidate: PrefilteredStory,
        stored: StoredJudgement,
        inputs: ScanInputs,
        existing: NewsSignal | None,
    ) -> _Evaluated:
        ctx, now, bundle = inputs.ctx, inputs.now, candidate.bundle
        judgement = stored.judgement
        story = bundle.story
        suppression: str | None = None
        if judgement.event_date is not None:
            cutoff = (now - timedelta(days=self._recency_days(judgement, ctx))).date()
            if judgement.event_date < cutoff:
                suppression = "stale_resurfaced"
        credibility = self._credibility.assess(bundle.items, judgement, ctx)
        verdict = self._credibility.verdict(credibility, judgement, ctx)
        if suppression is None and not verdict.passed:
            suppression = verdict.reason
        safety = self._safety.evaluate(
            SafetyInput(judgement, credibility, bundle.items, ctx, self._alarm_words)
        )
        if suppression is None and safety.decision == "block":
            suppression = "safety_block"
        rel = relevance_score(judgement, ctx.freshness.watch_entities)
        decay = decay_for(judgement.event_type, self._half_lives)
        fresh = freshness(story.first_published_at, now, decay.half_life_hours)
        expiry = expires_at(judgement, story.first_published_at, decay)
        if suppression is None and rel < self._settings.relevance_min_for_opportunity:
            suppression = "low_relevance"
        if suppression is None and expiry <= now:
            suppression = "expired"
        demand: DemandOutcome | None = None
        if suppression is None and rel >= self._settings.relevance_min_for_demand:
            demand = await self._demand_for(ctx, stored, bundle, inputs)
        demand_check = demand.check if demand else self._unchecked_demand(stored, bundle, now)
        if suppression is None and self._already_covered(
            ctx, judgement.key_entity.canonical, stored.aliases, story.first_published_at
        ):
            suppression = "client_already_covered"
        refresh_ok = (
            judgement.confirmation is ConfirmationStatus.confirmed
            and TIER_RANK[credibility.best_tier] >= TIER_RANK[SourceTier.tier2_established]
            and (not ctx.is_health or credibility.has_authoritative_health_source)
            and judgement.relevance_band in REFRESH_BANDS
            and safety.decision == "allow"
            and expiry > now
            and verdict.passed
        )
        verification = existing.verification_status if existing else "active"
        if judgement.conflicting_reports and verification == "active":
            verification = "disputed"
        signal = NewsSignal(
            id=signal_id_for(ctx.client_id, story.id),
            client_id=ctx.client_id,
            story_id=story.id,
            profile_version=ctx.profile_version,
            judgement=judgement,
            credibility=credibility,
            demand=demand_check,
            aliases=stored.aliases,
            is_competitor_entity=bool(judgement.about_competitor_ids),
            first_seen_at=story.first_published_at,
            sources=reference_sources(bundle.items),
            verification_status=verification,
            superseded_by=existing.superseded_by if existing else None,
            relevance_score=rel,
            urgency=urgency(fresh, demand_check, judgement.event_type),
            half_life_hours=decay.half_life_hours,
            expires_at=expiry,
            refresh_keywords=stored.refresh_keywords,
            refresh_eligible=refresh_ok and verification == "active",
            safety=safety,
            status="suppressed"
            if suppression
            else (existing.status if existing and existing.status != "suppressed" else "new"),
            suppression_reason=suppression,
            created_at=existing.created_at if existing else now,
        )
        emit = (
            suppression is None
            and safety.decision in EMIT_DECISIONS
            and verification == "active"
            and corroborated(credibility, ctx, 1)
        )
        return _Evaluated(signal, bundle, demand, emit, fresh)

    def _rank(self, evaluated: _Evaluated) -> RankedCandidate:
        signal = evaluated.signal
        return RankedCandidate(
            key=str(signal.id),
            family=entity_family(signal.entity),
            story_id=str(signal.story_id),
            band=signal.judgement.relevance_band,
            credibility=signal.credibility,
            freshness=evaluated.fresh,
            saturation=signal.demand.saturation,
        )

    async def scan(self, inputs: ScanInputs) -> ScanOutcome:
        ctx, now = inputs.ctx, inputs.now
        outcome = ScanOutcome()
        existing = {s.story_id: s for s in inputs.existing_signals}
        candidates = self._prefilter.select(
            inputs.bundles, ctx, now, business_vector=inputs.business_vector, centroids=inputs.centroids
        )
        evaluated: list[_Evaluated] = []
        for candidate in candidates:
            stored = await self._judgement(candidate, inputs, outcome)
            if stored is None:
                continue
            evaluated.append(
                await self._evaluate(candidate, stored, inputs, existing.get(candidate.bundle.story.id))
            )
        evaluated_ids = {e.signal.id for e in evaluated}
        carried = [s for s in inputs.existing_signals if s.id not in evaluated_ids]
        current = {e.signal.id: e for e in evaluated}
        all_signals = [e.signal for e in evaluated] + carried
        changes = [
            *self._lifecycle.retractions(all_signals, inputs.recent_items),
            *self._lifecycle.supersessions(all_signals, [e.signal for e in evaluated]),
            *self._lifecycle.expiries(all_signals, now),
        ]
        by_id = {s.id: s for s in all_signals}
        for change in changes:
            updated = self._lifecycle.apply(by_id[change.signal_id], change)
            by_id[change.signal_id] = updated
            if change.signal_id in current:
                current[change.signal_id].signal = updated
                current[change.signal_id].emit = (
                    current[change.signal_id].emit
                    and updated.verification_status == "active"
                    and updated.status != "expired"
                )
            if updated.verification_status != "active" or updated.status == "expired":
                outcome.withdrawals.append(self._lifecycle.withdrawal(updated, change.reason))
        outcome.changes = changes
        emitted = [e for e in current.values() if e.emit]
        cap = ctx.envelope(self._settings).max_opportunities_per_day
        kept, capped = flood_control(emitted, self._rank, cap)
        for item in capped:
            item.signal = item.signal.model_copy(update={"status": "suppressed", "suppression_reason": "cap"})
            by_id[item.signal.id] = item.signal
        history = self._history.estimate(ctx, inputs.past_entities, now)
        for item in kept:
            draft = self._builder.build(
                OpportunityInputs(
                    signal=item.signal,
                    items=item.bundle.items,
                    videos=item.demand.videos if item.demand else {},
                    expected_range=history.expected_range,
                    demo_event_types=self._demo_events,
                    health=ctx.is_health,
                ),
                ctx,
                now,
            )
            result = self._gate.check(draft)
            if result.action is GateAction.pass_:
                draft = draft.model_copy(update={"gate_results": [*draft.gate_results, result]})
                outcome.drafts.append(draft)
                outcome.gate_results.append(result)
                signal = item.signal.model_copy(update={"status": "drafted"})
                if "review_required" in draft.flags:
                    outcome.review.append((signal.id, "health_review" if ctx.is_health else "safety_review"))
            else:
                outcome.gate_suppressed.append((draft, result))
                signal = item.signal.model_copy(
                    update={"status": "suppressed", "suppression_reason": f"gate:{result.reason_code}"}
                )
            by_id[signal.id] = signal
            if item.demand is not None:
                outcome.demand_videos[signal.id] = item.demand
        outcome.signals = list(by_id.values())
        return outcome
