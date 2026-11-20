from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from app.core.config import RecommendSettings
from app.core.logging import get_logger
from app.domain.card_copy import CardCopy, Violation
from app.domain.common import DomainModel
from app.domain.recommendations import (
    Recommendation,
    RecommendationDraft,
    RecommendationSet,
    RecType,
    ReferenceVideo,
    RelatedRecommendation,
    SuppressedRecommendation,
    WhiteSpaceNote,
    recommendation_id,
)
from app.domain.refresh import DescriptionRefresh
from app.services.recommend import registrations as _registrations  # noqa: F401 - registers recommenders and gates
from app.services.recommend.annotate import DraftAnnotator
from app.services.recommend.assembly import AssembledCard, ContractAssembler
from app.services.recommend.builder import ContextBuilder
from app.services.recommend.context import RecommendContext
from app.services.recommend.creators.ranker import (
    CreatorList,
    CreatorListEntry,
    order_creators,
    relevance_group,
    sort_value,
)
from app.services.recommend.evidence.index import OwnVideo
from app.services.recommend.evidence.references import ReferenceSelector
from app.services.recommend.gaps import GapReport
from app.services.recommend.gates.base import ChainResult, GateChain, GateEnv, GateOutcome, Phase
from app.services.recommend.gates.structural import PolicyGuardrailsStructural
from app.services.recommend.inputs import RecommendInputs
from app.services.recommend.lens import LensAssigner
from app.services.recommend.merge import CandidateMerger
from app.services.recommend.recommenders.base import enabled_recommenders
from app.services.recommend.scoring.diversity import MmrSelector, Selection
from app.services.recommend.scoring.scorer import Scorer
from app.services.recommend.surfaces import EarlySignal, HygieneItem, early_signal, hygiene_item, route
from app.services.recommend.text.copying import CopyDetector
from app.services.recommend.text.lexicon import Lexicon
from app.services.recommend.thin_data import ThinDataAdvisor, WindowSummary
from app.services.recommend.white_space import WhiteSpaceDetector
from app.services.recommend.writer import CardRequest, CardWriter, TemplateCardRenderer, WriterFacts

WINDOWS = (7, 30, 90)
# creators follow one visible sort and refreshes their own surface; only these are ranked by the card score
SCORED_TYPES = frozenset(set(RecType) - {RecType.creator, RecType.description_refresh})
SuppressionPhase = Literal["pre_writer", "post_writer", "set_level"]
logger = get_logger(__name__)


class WindowOutput(DomainModel):
    set: RecommendationSet
    creator_list: CreatorList
    refresh_cards: list[Recommendation] = []
    early_signals: list[EarlySignal] = []
    channel_hygiene: list[HygieneItem] = []
    gap_report: GapReport
    writer: Literal["llm", "template"]


class RecommendOutput(DomainModel):
    client_id: UUID
    as_of: datetime
    generated_at: datetime
    selected_window: int
    windows: list[WindowOutput]

    def window(self, window_days: int) -> WindowOutput | None:
        return next((item for item in self.windows if item.set.window_days == window_days), None)


@dataclass(slots=True)
class WindowPlan:
    ctx: RecommendContext
    env: GateEnv
    candidate_count: int
    selection: Selection
    creators: list[RecommendationDraft]
    refresh: list[RecommendationDraft]
    early: list[EarlySignal]
    hygiene: list[HygieneItem]
    suppressed: list[SuppressedRecommendation]
    summary: WindowSummary
    white_space: list[WhiteSpaceNote]
    matched_topics: int
    refreshes: dict[str, DescriptionRefresh] = field(default_factory=dict)
    run_id: UUID | None = None


@dataclass(slots=True)
class RecommendRun:
    output: RecommendOutput
    cards: list[AssembledCard] = field(default_factory=list)
    suppressed: list[SuppressedRecommendation] = field(default_factory=list)
    writer_fallbacks: int = 0
    degraded_gate: str | None = None
    own_videos: list[OwnVideo] = field(default_factory=list)


def _scalars(details: dict[str, Any]) -> dict[str, float | int | str | bool]:
    return {key: value for key, value in details.items() if isinstance(value, bool | int | float | str)}


def suppression(
    draft: RecommendationDraft,
    phase: SuppressionPhase,
    gate: str,
    reason: str,
    at: datetime,
    details: dict[str, Any] | None = None,
) -> SuppressedRecommendation:
    return SuppressedRecommendation(
        draft=draft,
        phase=phase,
        gate=gate,
        reason_code=reason,
        details=_scalars(details or {}),
        created_at=at,
    )


def violations(result: ChainResult) -> list[Violation]:
    return [
        Violation(
            rule=outcome.gate,
            field="card",
            token=", ".join(str(value) for value in _scalars(outcome.details).values()) or None,
            detail=outcome.reason_code or outcome.gate,
        )
        for outcome in result.violations
    ]


class RecommendationService:
    """runs the recommend flow: candidates, merge, annotate, gate, score, diversify, write, gate again, assemble."""

    def __init__(self, settings: RecommendSettings) -> None:
        self.settings = settings
        self.builder = ContextBuilder(settings)
        self.references = ReferenceSelector()
        self.annotator = DraftAnnotator(self.references, LensAssigner())
        self.merger = CandidateMerger()
        self.scorer = Scorer()
        self.assembler = ContractAssembler()
        self.template = TemplateCardRenderer()
        self.pre_chain = GateChain(Phase.pre_writer)
        self.post_chain = GateChain(Phase.post_writer, stop_on_suppress=False)

    async def generate(
        self,
        inputs: RecommendInputs,
        *,
        run_id: UUID,
        generated_at: datetime,
        writer: CardWriter | None = None,
    ) -> RecommendRun:
        plans = {window: plan for window in WINDOWS if (plan := self.plan(inputs, window)) is not None}
        run = RecommendRun(
            output=RecommendOutput(
                client_id=inputs.client_id,
                as_of=inputs.as_of,
                generated_at=generated_at,
                selected_window=inputs.selected_window,
                windows=[],
            )
        )
        windows: list[WindowOutput] = []
        for window, plan in plans.items():
            use_llm = writer is not None and window == inputs.selected_window
            wider = [plans[other].summary for other in sorted(plans) if other > window]
            output, cards, fallbacks = await self.finish(
                plan, writer if use_llm and writer else self.template, run_id, generated_at, wider, use_llm
            )
            windows.append(output)
            run.cards.extend(cards)
            run.suppressed.extend(plan.suppressed)
            if window == inputs.selected_window:
                run.writer_fallbacks = fallbacks
                run.degraded_gate = self._degraded_gate(plan)
                run.own_videos = list(plan.ctx.index.own_videos)
        run.output = run.output.model_copy(update={"windows": windows})
        return run

    def plan(self, inputs: RecommendInputs, window_days: int) -> WindowPlan | None:
        ctx = self.builder.build(inputs, window_days)
        if ctx is None:
            return None
        env = self._env(ctx)
        at = ctx.as_of
        suppressed: list[SuppressedRecommendation] = []
        drafts: list[RecommendationDraft] = []
        for recommender in enabled_recommenders(ctx.disabled_types):
            drafts.extend(recommender.generate(ctx))
        external = [
            draft
            for draft in [*inputs.news_drafts, *inputs.refresh_drafts]
            if draft.window_days == window_days and draft.client_id == inputs.client_id
        ]
        candidates = [*drafts, *external]
        merged = self.merger.merge(candidates, ctx)
        suppressed += [
            suppression(d, "pre_writer", "candidate_merger", code, at, info)
            for d, code, info in merged.dropped
        ]
        annotated = [self.annotator.annotate(draft, ctx) for draft in merged.kept]
        routed = route(
            annotated, {rec_type: ctx.settings.gates.min_refs(rec_type.value) for rec_type in RecType}
        )
        early, hygiene = self._side_surfaces(routed.early, routed.hygiene, env, suppressed)

        gated = self._pre_gate([*routed.main, *routed.refresh], env, suppressed)

        refresh = self._refresh_cards(gated, inputs, suppressed, at)
        creators_all = order_creators(
            [draft for draft in gated if draft.type is RecType.creator],
            ctx.config.creator_sort or ctx.settings.creators.default_sort,
            ctx.settings.creators.strong_match_share,
        )
        scored = self._score([draft for draft in gated if draft.type in SCORED_TYPES], ctx, suppressed)

        target = ctx.settings.cards_per_window.get(window_days, 0)
        creator_cap = ctx.settings.type_caps.get(RecType.creator.value, 0)
        creator_slots = min(creator_cap, len(creators_all), max(1, target // 3))
        selector = MmrSelector(
            ctx,
            self.references,
            lambda draft, refs: self._reselect(draft, refs, env),
        )
        selection = selector.select(scored, target - creator_slots)
        suppressed += [
            suppression(draft, "set_level", "reference_reuse", reason, at)
            for draft, reason in selection.skipped
            if reason == "ref_overused"
        ]
        creator_take = min(creator_cap, len(creators_all), target - len(selection.selected))
        creators = creators_all[:creator_take]

        index = ctx.index
        outlier_channels = {video.channel_id for video in index.outliers}
        planned = [*selection.selected, *creators]
        summary = WindowSummary(
            window_days=window_days,
            outliers=len(index.outliers),
            channels=len(outlier_channels),
            videos=len(index.videos),
            cards=len(planned),
            type_cards=dict(Counter(draft.type for draft in planned)),
        )
        widest = ctx.creators.index_90
        return WindowPlan(
            ctx=ctx,
            env=env,
            candidate_count=len(candidates),
            selection=selection,
            creators=creators,
            refresh=refresh,
            early=early,
            hygiene=hygiene,
            suppressed=suppressed,
            summary=summary,
            white_space=WhiteSpaceDetector().notes(ctx.catalog, widest, window_days),
            matched_topics=sum(1 for topic in ctx.catalog.all() if widest.by_topic(topic.id)),
            refreshes={refresh.video_id: refresh for refresh in inputs.refreshes},
        )

    def _pre_gate(
        self, drafts: list[RecommendationDraft], env: GateEnv, suppressed: list[SuppressedRecommendation]
    ) -> list[RecommendationDraft]:
        gated = []
        for draft in drafts:
            result = self.pre_chain.run(draft, env)
            stopped = result.stopped
            if stopped is None:
                gated.append(result.draft)
                continue
            reason = stopped.reason_code or stopped.gate
            suppressed.append(
                suppression(result.draft, "pre_writer", stopped.gate, reason, env.ctx.as_of, stopped.details)
            )
        return gated

    def _score(
        self,
        drafts: list[RecommendationDraft],
        ctx: RecommendContext,
        suppressed: list[SuppressedRecommendation],
    ) -> list[RecommendationDraft]:
        kept = []
        for draft in drafts:
            scored = self.scorer.score(draft, ctx)
            total = scored.score.total if scored.score else 0.0
            if total < ctx.settings.min_score:
                suppressed.append(
                    suppression(scored, "set_level", "scorer", "below_min_score", ctx.as_of, {"total": total})
                )
            else:
                kept.append(scored)
        return kept

    async def finish(
        self,
        plan: WindowPlan,
        writer: CardWriter,
        run_id: UUID,
        generated_at: datetime,
        wider: list[WindowSummary],
        use_llm: bool,
    ) -> tuple[WindowOutput, list[AssembledCard], int]:
        ctx = plan.ctx
        plan.run_id = run_id
        main, fallbacks = await self._write_and_gate([*plan.selection.selected, *plan.creators], writer, plan)
        freed = len(plan.selection.selected) + len(plan.creators) - len(main)
        if freed > 0 and plan.selection.queue:
            # one backfill round from the diversity queue for slots the post-writer gates freed
            kept_scored = [draft for draft, _ in main if draft.type is not RecType.creator]
            backfill = Selection(selected=list(kept_scored), queue=list(plan.selection.queue))
            selector = MmrSelector(
                ctx,
                self.references,
                lambda draft, refs: self._reselect(draft, refs, plan.env),
            )
            selector.fill(backfill, len(kept_scored) + freed)
            extra, extra_fallbacks = await self._write_and_gate(
                backfill.selected[len(kept_scored) :], writer, plan
            )
            main = [
                *[item for item in main if item[0].type is not RecType.creator],
                *extra,
                *[item for item in main if item[0].type is RecType.creator],
            ]
            fallbacks += extra_fallbacks
        refresh, refresh_fallbacks = await self._write_and_gate(
            plan.refresh[: ctx.settings.refresh_surface_cap], writer, plan
        )
        fallbacks += refresh_fallbacks
        refresh_cards = [
            self.assembler.assemble(
                draft,
                copy,
                ctx,
                run_id=run_id,
                generated_at=generated_at,
                related=[],
                refresh=plan.refreshes.get(draft.subject.video_id),
            )
            for draft, copy in refresh
        ]
        main_cards = [
            self.assembler.assemble(
                draft,
                copy,
                ctx,
                run_id=run_id,
                generated_at=generated_at,
                related=self._related(draft, refresh),
            )
            for draft, copy in main
        ]
        cards = [card.recommendation for card in main_cards]
        final = WindowSummary(
            window_days=plan.summary.window_days,
            outliers=plan.summary.outliers,
            channels=plan.summary.channels,
            videos=plan.summary.videos,
            cards=len(cards),
            type_cards=dict(Counter(card.type for card in cards)),
        )
        notices = ThinDataAdvisor().notices(ctx, final, wider, plan.matched_topics)
        counts = Counter(item.gate for item in plan.suppressed)
        recommendation_set = RecommendationSet(
            client_id=ctx.client_id,
            window_days=ctx.window_days,
            as_of=ctx.as_of,
            generated_at=generated_at,
            recommendations=cards,
            notices=notices,
            white_space=plan.white_space,
            suppressed_counts=dict(sorted(counts.items())),
            candidate_count=plan.candidate_count,
        )
        output = WindowOutput(
            set=recommendation_set,
            creator_list=self._creator_list(
                ctx, [draft for draft, _ in main if draft.type is RecType.creator]
            ),
            refresh_cards=[card.recommendation for card in refresh_cards],
            early_signals=plan.early[: ctx.settings.early_signal_cap],
            channel_hygiene=plan.hygiene[: ctx.settings.hygiene_cap],
            gap_report=ctx.gaps,
            writer="llm" if use_llm else "template",
        )
        return output, [*main_cards, *refresh_cards], fallbacks

    async def _write_and_gate(
        self, drafts: Sequence[RecommendationDraft], writer: CardWriter, plan: WindowPlan
    ) -> tuple[list[tuple[RecommendationDraft, CardCopy]], int]:
        """writer first, one rewrite with the violations, then the template; a card that still fails is not shown."""
        if not drafts:
            return [], 0
        ctx, env = plan.ctx, plan.env
        client = ctx.client
        facts = self._writer_facts(plan)
        written = await self._safe_write(
            writer, [CardRequest(draft, client, facts, plan.run_id) for draft in drafts]
        )
        fallbacks = sum(
            1 for draft in drafts if draft.draft_id not in written and writer is not self.template
        )
        accepted: dict[str, tuple[RecommendationDraft, CardCopy]] = {}
        retry: list[tuple[RecommendationDraft, list[Violation]]] = []
        for draft in drafts:
            copy = written.get(draft.draft_id) or self.template.render(draft, client)
            result = self.post_chain.run(draft, env, copy)
            if not result.violations:
                accepted[draft.draft_id] = (result.draft, copy)
            elif copy.copy_source != "template":
                retry.append((draft, violations(result)))
            else:
                self._reject(plan, result, copy)
        if retry:
            rewritten = await self._safe_write(
                writer,
                [CardRequest(draft, client, facts, plan.run_id, tuple(found)) for draft, found in retry],
            )
            for draft, _ in retry:
                copy = rewritten.get(draft.draft_id)
                result = self.post_chain.run(draft, env, copy) if copy else None
                if copy is not None and result is not None and not result.violations:
                    accepted[draft.draft_id] = (result.draft, copy)
                    continue
                fallbacks += 1
                template = self.template.render(draft, client)
                result = self.post_chain.run(draft, env, template)
                if result.violations:
                    self._reject(plan, result, template)
                else:
                    accepted[draft.draft_id] = (result.draft, template)
        return [accepted[draft.draft_id] for draft in drafts if draft.draft_id in accepted], fallbacks

    @staticmethod
    def _writer_facts(plan: WindowPlan) -> WriterFacts:
        ctx = plan.ctx
        channels = {video.channel_title for video in ctx.index.videos}
        return WriterFacts(
            buyer_questions={
                topic.buyer_question.id: topic.label
                for topic in ctx.catalog.buyer_questions()
                if topic.buyer_question
            },
            topic_labels={topic.id: topic.label for topic in ctx.catalog.all()},
            recent_titles=tuple(video.title for video in ctx.index.own_videos),
            known_channel_names=tuple(sorted(channels | {competitor.name for competitor in ctx.competitors})),
        )

    def _reselect(
        self, draft: RecommendationDraft, refs: list[ReferenceVideo], env: GateEnv
    ) -> RecommendationDraft | None:
        result = self.pre_chain.run(self.annotator.annotate(draft, env.ctx, refs), env)
        if result.stopped is not None:
            return None
        return self.scorer.score(result.draft, env.ctx)

    def _reject(self, plan: WindowPlan, result: ChainResult, copy: CardCopy) -> None:
        first: GateOutcome = result.violations[0]
        reason = first.reason_code or first.gate
        if copy.copy_source == "template" and reason == "copy_risk":
            reason = "copy_risk_unresolvable"
        plan.suppressed.append(
            suppression(result.draft, "post_writer", first.gate, reason, plan.ctx.as_of, first.details)
        )

    @staticmethod
    async def _safe_write(writer: CardWriter, requests: list[CardRequest]) -> dict[str, CardCopy]:
        try:
            return dict(await writer.write(requests))
        except Exception as error:  # noqa: BLE001 - any writer failure degrades to the template, never blocks
            logger.warning("recommend.writer_failed", error=type(error).__name__, detail=str(error)[:200])
            return {}

    def _side_surfaces(
        self,
        early_drafts: list[RecommendationDraft],
        hygiene_drafts: list[RecommendationDraft],
        env: GateEnv,
        suppressed: list[SuppressedRecommendation],
    ) -> tuple[list[EarlySignal], list[HygieneItem]]:
        policy = PolicyGuardrailsStructural()
        at = env.ctx.as_of
        early: list[EarlySignal] = []
        for draft in early_drafts:
            outcome = policy.check(draft, env, None)
            if outcome.reason_code:
                suppressed.append(suppression(draft, "pre_writer", outcome.gate, outcome.reason_code, at))
                continue
            signal = early_signal(draft, at, env.ctx.settings.early_signal_min_sources)
            if isinstance(signal, str):
                suppressed.append(suppression(draft, "pre_writer", "early_signals", signal, at))
            else:
                early.append(signal)
        hygiene: list[HygieneItem] = []
        for draft in hygiene_drafts:
            outcome = policy.check(draft, env, None)
            if outcome.reason_code:
                suppressed.append(suppression(draft, "pre_writer", outcome.gate, outcome.reason_code, at))
            else:
                hygiene.append(hygiene_item(draft))
        return early, hygiene

    @staticmethod
    def _refresh_cards(
        gated: list[RecommendationDraft],
        inputs: RecommendInputs,
        suppressed: list[SuppressedRecommendation],
        at: datetime,
    ) -> list[RecommendationDraft]:
        known = {refresh.video_id for refresh in inputs.refreshes}
        cards = []
        for draft in gated:
            if draft.type is not RecType.description_refresh:
                continue
            if draft.subject.video_id in known:
                cards.append(draft)
            else:
                suppressed.append(
                    suppression(draft, "pre_writer", "refresh_details", "refresh_details_missing", at)
                )
        return cards

    @staticmethod
    def _related(
        draft: RecommendationDraft, refresh: list[tuple[RecommendationDraft, CardCopy]]
    ) -> list[RelatedRecommendation]:
        if draft.type is not RecType.video_idea:
            return []
        topics = set(draft.topic_ids)
        return [
            RelatedRecommendation(type=RecType.description_refresh, id=recommendation_id(item.stable_key))
            for item, _ in refresh
            if topics & set(item.topic_ids)
        ]

    def _creator_list(self, ctx: RecommendContext, drafts: list[RecommendationDraft]) -> CreatorList:
        criterion = ctx.config.creator_sort or ctx.settings.creators.default_sort
        strong = ctx.settings.creators.strong_match_share
        groups: dict[str, list[CreatorListEntry]] = {"strong": [], "related": []}
        for draft in order_creators(drafts, criterion, strong):
            subject = draft.subject
            group = relevance_group(subject, strong)
            groups[group].append(
                CreatorListEntry(
                    channel_id=subject.channel_id,
                    recommendation_id=recommendation_id(draft.stable_key),
                    group=group,
                    sort_value=sort_value(subject, criterion),
                )
            )
        return CreatorList(window_days=ctx.window_days, sort=criterion, groups=groups)

    def _env(self, ctx: RecommendContext) -> GateEnv:
        settings = ctx.settings
        others = ctx.creators.index_90.all_titles
        own = [video.title for video in ctx.index.own_videos]
        domain_policy = (
            Lexicon(settings.healthcare_lexicon) if ctx.client.domain_kind == "healthcare" else None
        )
        return GateEnv(
            ctx=ctx,
            copy=CopyDetector(others, own, settings.no_copy),
            policy=Lexicon(settings.policy_lexicon),
            domain_policy=domain_policy,
            certainty=Lexicon(settings.certainty_lexicon),
            references=self.references,
            reannotate=lambda draft, refs: self.annotator.annotate(draft, ctx, refs),
        )

    def _degraded_gate(self, plan: WindowPlan) -> str | None:
        # every candidate suppressed by one gate points at a bug or bad config, not at thin data
        counts = Counter(item.gate for item in plan.suppressed)
        if plan.candidate_count <= plan.ctx.settings.degraded_min_candidates:
            return None
        return next((gate for gate, count in counts.items() if count == plan.candidate_count), None)
