from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from app.core.config import TopicEngineSettings
from app.core.db import UnitOfWork
from app.core.tenancy import TenantScope
from app.domain.topics import (
    AnswerDirectness,
    AssignedTopic,
    MatchMethod,
    PreparedVideoText,
    ReviewReason,
    TopicKind,
    TopicMatch,
    TopicRef,
    TopicUniverse,
    VideoTopicAssignment,
)
from app.ports.llm import LLMClient
from app.services.topics.directness.scorer import AnswerDirectnessScorer, DirectnessInput
from app.services.topics.lexicon import Lexicons
from app.services.topics.llm import SpendGuard
from app.services.topics.matching.adjudicator import AdjudicationItem, AdjudicationStats, LLMAdjudicator
from app.services.topics.matching.assign import ENTITY_KINDS, TopicAssigner
from app.services.topics.matching.base import MatchContext, Matcher, MatcherResult, Pair
from app.services.topics.matching.combine import DecisionPolicy, PairState, ScoreCombiner
from app.services.topics.review.explain import MatchExplainer
from app.services.topics.review.flagger import ReviewFlagger
from app.services.topics.review.queue import OverrideRule, apply_rules
from app.services.topics.universe.expansion.base import CorpusFrequencies

RunMode = Literal["cold", "daily"]
DEGRADED_VALUES = ("no_llm", "no_embedder", "llm_budget")
REVIEWABLE_REJECTIONS = frozenset({ReviewReason.llm_quote_unverified})


@dataclass(frozen=True)
class VideoMeta:
    video_id: str
    channel_id: str
    published_at: datetime
    observed_at: datetime
    expires_at: datetime
    is_short: bool = False
    own: bool = False
    in_window: bool = True


@dataclass
class PipelineOutput:
    matches: list[TopicMatch] = field(default_factory=list)
    traces: dict[Pair, dict[str, Any]] = field(default_factory=dict)
    assignments: list[VideoTopicAssignment] = field(default_factory=list)
    directness: list[AnswerDirectness] = field(default_factory=list)
    review_items: list[dict[str, Any]] = field(default_factory=list)
    adjudication: AdjudicationStats = field(default_factory=AdjudicationStats)
    implicit_rejections: int = 0
    degraded: set[str] = field(default_factory=set)
    unsupported_videos: list[str] = field(default_factory=list)


class MatchPipeline:
    """prepared texts in, client-scoped topic matches, directness and review items out."""

    def __init__(
        self,
        settings: TopicEngineSettings,
        lexicons: Lexicons,
        matchers: Sequence[Matcher],
        combiner: ScoreCombiner,
        *,
        engine_version: str,
        llm: LLMClient | None,
        guard: SpendGuard,
        model_key: str,
        locations: Sequence[str] = (),
        uow_factory: Callable[[], UnitOfWork] | None = None,
        scope: TenantScope | None = None,
    ) -> None:
        self._settings = settings
        self._lexicons = lexicons
        self._matchers = sorted(matchers, key=lambda matcher: matcher.order)
        self._policy = DecisionPolicy(settings, combiner)
        self._combiner = combiner
        self._engine_version = engine_version
        self._llm = llm
        self._guard = guard
        self._model_key = model_key
        self._locations = list(locations)
        self._uow_factory = uow_factory
        self._scope = scope
        self._assigner = TopicAssigner(settings)
        self._explainer = MatchExplainer()
        self._directness = AnswerDirectnessScorer(lexicons)

    async def run(
        self,
        universe: TopicUniverse,
        non_topical_terms: Sequence[str],
        videos: Sequence[PreparedVideoText],
        meta: Mapping[str, VideoMeta],
        corpus: CorpusFrequencies,
        *,
        mode: RunMode,
        now: datetime,
        run_id: UUID | None = None,
        overrides: Mapping[str, Sequence[OverrideRule]] | None = None,
    ) -> PipelineOutput:
        ctx = MatchContext(
            universe=universe,
            settings=self._settings,
            lexicons=self._lexicons,
            non_topical_terms=non_topical_terms,
            corpus=corpus,
        )
        output = PipelineOutput()
        if not videos or not ctx.refs:
            output.assignments = [
                VideoTopicAssignment(
                    video_id=video.video_id,
                    client_id=universe.client_id,
                    universe_hash=universe.universe_hash,
                    unmatched_reason="below_threshold",
                )
                for video in videos
            ]
            return output
        results: dict[str, dict[Pair, MatcherResult]] = {}
        for matcher in self._matchers:
            results[matcher.name] = await matcher.score(videos, ctx)
        states = self._evaluate(videos, ctx, results)
        output.implicit_rejections = len(videos) * len(ctx.refs) - sum(
            len(pairs) for pairs in states.values()
        )
        output.adjudication = await self._adjudicate(videos, states, meta, universe.client_id, run_id, mode)
        overrides = overrides or {}
        for video in videos:
            assignment = self._assigner.assign(video, states.get(video.video_id, []), ctx.refs_by_id)
            matches = [self._match(state, video, universe, now, output.traces) for state in assignment.pairs]
            matches = self._apply_overrides(
                matches, overrides.get(video.video_id, ()), video, universe, ctx, now
            )
            matches = rerank(matches, ctx.refs_by_id)
            matches = [self._explained(match, video, ctx) for match in matches]
            output.matches += matches
            output.assignments.append(_assignment(video, matches, universe, assignment.unmatched_reason))
            output.directness += self._score_directness(
                video, matches, states.get(video.video_id, []), ctx, meta, universe
            )
            if ctx.semantic_missing.get(video.video_id) == "unsupported_script" and not any(
                match.decision == "accepted" for match in matches
            ):
                output.unsupported_videos.append(video.video_id)
        output.degraded = {
            value for match in output.matches for value in match.degraded
        } | output.adjudication.degraded
        if any(reason == "no_embedder" for reason in ctx.semantic_missing.values()):
            output.degraded.add("no_embedder")
        reviewable = [
            match
            for match in output.matches
            if match.decision != "rejected" or set(match.review_reasons) & REVIEWABLE_REJECTIONS
        ]
        output.review_items = ReviewFlagger().items(reviewable)
        return output

    def _evaluate(
        self,
        videos: Sequence[PreparedVideoText],
        ctx: MatchContext,
        results: dict[str, dict[Pair, MatcherResult]],
    ) -> dict[str, list[PairState]]:
        states: dict[str, list[PairState]] = {}
        lexical, semantic = results.get("lexical", {}), results.get("semantic", {})
        for video in videos:
            for ref_id in sorted(ctx.candidates.get(video.video_id, set())):
                pair = (video.video_id, ref_id)
                state = self._policy.evaluate(
                    video,
                    ctx.refs_by_id[ref_id],
                    lexical.get(pair),
                    semantic.get(pair),
                    ctx.semantic_missing.get(video.video_id),
                )
                states.setdefault(video.video_id, []).append(state)
            self._sibling_guard(states.get(video.video_id, []))
        return states

    def _sibling_guard(self, states: list[PairState]) -> None:
        # a semantic-only match next to a lexically matched sibling mostly echoes that sibling
        covered: set[str] = set()
        for state in states:
            if state.lexical_score > 0 and state.ref.kind in ENTITY_KINDS:
                covered |= {state.ref.id, *state.ref.offering_ids}
        for state in states:
            related = set(state.ref.offering_ids) | set(state.ref.parent_ids)
            echoes = related & covered and state.ref.id not in covered
            if state.lexical_score == 0 and state.decision == "accepted" and echoes:
                self._policy.force(state, ReviewReason.semantic_only)

    async def _adjudicate(
        self,
        videos: Sequence[PreparedVideoText],
        states: dict[str, list[PairState]],
        meta: Mapping[str, VideoMeta],
        client_id: UUID,
        run_id: UUID | None,
        mode: RunMode,
    ) -> AdjudicationStats:
        items = []
        for video in videos:
            pending = [state for state in states.get(video.video_id, []) if state.decision == "pending"]
            if not pending:
                continue
            info = meta[video.video_id]
            priority = (int(not info.in_window) * 2 + int(not info.own), -info.published_at.timestamp())
            items.append(AdjudicationItem(video, pending, info.observed_at, info.expires_at, priority))
        if not items:
            return AdjudicationStats()
        adjudicator = LLMAdjudicator(
            self._llm,
            self._settings,
            self._policy,
            self._guard,
            model_key=self._model_key,
            client_id=client_id,
            run_id=run_id,
            uow_factory=self._uow_factory,
            scope=self._scope,
        )
        stats = await adjudicator.adjudicate(items, self._settings.adj_max_videos_per_run[mode])
        # candidates beyond adj_max_candidates were never sent; they wait like over-budget items
        for pending in states.values():
            for state in pending:
                if state.decision == "pending":
                    self._policy.without_llm(state, "llm_budget")
        return stats

    def _match(
        self,
        state: PairState,
        video: PreparedVideoText,
        universe: TopicUniverse,
        now: datetime,
        traces: dict[Pair, dict[str, Any]],
    ) -> TopicMatch:
        decision = "unadjudicated" if state.decision == "pending" else state.decision
        traces[(video.video_id, state.ref.id)] = {
            "ref_fingerprint": state.ref.fingerprint,
            "prepared_hash": video.prepared_hash,
            "lexical": {"score": round(state.lexical_score, 4), "flags": sorted(state.lexical_flags)},
            "semantic": dict(state.semantic.detail) if state.semantic else None,
            "combine": {
                "combiner_version": self._combiner.version,
                "L": round(state.lexical_score, 4),
                "S": None if state.semantic_score is None else round(state.semantic_score, 4),
                "C": round(state.combined, 4),
                "band": state.band,
                "forced": [reason.value for reason in state.forced],
            },
            "llm": state.llm,
            "assign": {"implied_from": state.implied_from},
            "override": None,
        }
        return TopicMatch(
            client_id=universe.client_id,
            video_id=video.video_id,
            topic_ref_id=state.ref.id,
            topic_kind=state.ref.kind,
            profile_version=universe.profile_version,
            universe_hash=universe.universe_hash,
            prepared_hash=video.prepared_hash,
            engine_version=self._engine_version,
            decision=decision,
            lexical_score=_unit(state.lexical_score),
            semantic_score=None if state.semantic_score is None else _unit(state.semantic_score),
            combined_score=_unit(state.combined),
            method=state.method,
            confidence=state.confidence,
            implied_from=state.implied_from,
            evidence=state.evidence,
            review_reasons=list(state.review),
            degraded=[value for value in DEGRADED_VALUES if value in state.degraded],
            explanation="",
            matched_at=now,
        )

    def _apply_overrides(
        self,
        matches: list[TopicMatch],
        rules: Sequence[OverrideRule],
        video: PreparedVideoText,
        universe: TopicUniverse,
        ctx: MatchContext,
        now: datetime,
    ) -> list[TopicMatch]:
        def blank(ref_id: str) -> TopicMatch:
            return blank_match(ctx.refs_by_id[ref_id], video, universe, self._engine_version, now)

        return apply_rules(matches, rules, ctx.refs_by_id, blank, now)

    def _explained(self, match: TopicMatch, video: PreparedVideoText, ctx: MatchContext) -> TopicMatch:
        implied = ctx.refs_by_id.get(match.implied_from) if match.implied_from else None
        footer = any(span.reason == "boilerplate" for span in video.removed_spans)
        text = self._explainer.explain(
            match,
            ctx.refs_by_id[match.topic_ref_id],
            implied_label=implied.label if implied else None,
            footer_removed=footer,
        )
        return match.model_copy(update={"explanation": text})

    def _score_directness(
        self,
        video: PreparedVideoText,
        matches: Sequence[TopicMatch],
        states: Sequence[PairState],
        ctx: MatchContext,
        meta: Mapping[str, VideoMeta],
        universe: TopicUniverse,
    ) -> list[AnswerDirectness]:
        accepted = {match.topic_ref_id: match for match in matches if match.decision == "accepted"}
        if not accepted:
            return []
        semantic = {state.ref.id: state.semantic for state in states if state.semantic is not None}
        in_title = sum(
            1
            for match in accepted.values()
            if any(item.method == "lexical" and item.field == "title" for item in match.evidence)
        )
        info = meta.get(video.video_id)
        rows = []
        for question in ctx.refs:
            if question.kind is not TopicKind.buyer_question:
                continue
            parents = [accepted[parent] for parent in question.parent_ids if parent in accepted]
            own = accepted.get(question.id)
            if own is None and not parents:
                continue
            title_semantic = (
                float(semantic[question.id].detail.get("S_title", 0.0)) if question.id in semantic else 0.0
            )
            rows.append(
                self._directness.score(
                    DirectnessInput(
                        client_id=universe.client_id,
                        universe_hash=universe.universe_hash,
                        video=video,
                        question=question,
                        parent_evidence=[item for parent in parents for item in parent.evidence],
                        question_evidence=list(own.evidence) if own else [],
                        title_semantic=title_semantic,
                        refs_matched_in_title=in_title,
                        is_short=bool(info and info.is_short),
                        locations=self._locations,
                    )
                )
            )
        return rows


def _unit(value: float) -> float:
    return round(min(1.0, max(0.0, value)), 4)


def rerank(matches: list[TopicMatch], refs_by_id: Mapping[str, TopicRef]) -> list[TopicMatch]:
    accepted = sorted(
        (match for match in matches if match.decision == "accepted"),
        key=lambda match: (-match.combined_score, match.method is MatchMethod.implied, match.topic_ref_id),
    )
    entity = [match for match in accepted if refs_by_id[match.topic_ref_id].kind in ENTITY_KINDS]
    primary = _primary(entity)
    ranks = {match.topic_ref_id: index + 1 for index, match in enumerate(accepted)}
    return sorted(
        (
            match.model_copy(
                update={"rank": ranks.get(match.topic_ref_id, 0), "is_primary": match.topic_ref_id == primary}
            )
            for match in matches
        ),
        key=lambda match: (match.rank == 0, match.rank, match.topic_ref_id),
    )


def _primary(entity: Sequence[TopicMatch]) -> str | None:
    if not entity:
        return None

    def key(match: TopicMatch) -> tuple:
        lexical = [item for item in match.evidence if item.method == "lexical"]
        title = any(item.field == "title" for item in lexical)
        specificity = max((float(item.detail.get("specificity", 0.0)) for item in lexical), default=0.0)
        tokens = max((len((item.alias_text or "").split()) for item in lexical), default=0)
        return (-match.combined_score, not title, -specificity, -tokens, match.topic_ref_id)

    return min(entity, key=key).topic_ref_id


def _assignment(
    video: PreparedVideoText, matches: Sequence[TopicMatch], universe: TopicUniverse, unmatched: str | None
) -> VideoTopicAssignment:
    accepted = [match for match in matches if match.decision == "accepted"]
    primary = next((match.topic_ref_id for match in accepted if match.is_primary), None)
    return VideoTopicAssignment(
        video_id=video.video_id,
        client_id=universe.client_id,
        universe_hash=universe.universe_hash,
        topics=[
            AssignedTopic(
                topic_ref_id=match.topic_ref_id,
                kind=match.topic_kind,
                confidence=match.confidence,
                combined_score=match.combined_score,
                is_primary=match.is_primary,
            )
            for match in accepted
        ],
        primary_topic_id=primary,
        unmatched_reason=None if accepted else (unmatched or "below_threshold"),
    )


def blank_match(
    ref: TopicRef, video: PreparedVideoText, universe: TopicUniverse, engine_version: str, now: datetime
) -> TopicMatch:
    return TopicMatch(
        client_id=universe.client_id,
        video_id=video.video_id,
        topic_ref_id=ref.id,
        topic_kind=ref.kind,
        profile_version=universe.profile_version,
        universe_hash=universe.universe_hash,
        prepared_hash=video.prepared_hash,
        engine_version=engine_version,
        decision="rejected",
        lexical_score=0.0,
        combined_score=0.0,
        method=MatchMethod.override,
        confidence="high",
        explanation="",
        matched_at=now,
    )
