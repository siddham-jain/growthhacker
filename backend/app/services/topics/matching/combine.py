import math
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from app.core.config import TopicEngineSettings
from app.domain.topics import MatchEvidence, MatchMethod, PreparedVideoText, ReviewReason, TopicRef
from app.services.topics.matching.base import MatcherResult

Band = Literal["accept", "ambiguous", "reject"]
Decision = Literal["accepted", "rejected", "unadjudicated", "pending"]
Confidence = Literal["high", "medium", "low"]
CONFLICT_REASONS = frozenset(
    {ReviewReason.method_conflict, ReviewReason.negated_evidence, ReviewReason.ambiguous_alias}
)


class ScoreCombiner(Protocol):
    version: str

    def combine(self, lexical: float, semantic: float | None, features: dict[str, float]) -> float: ...


class NoisyOrCombiner:
    def __init__(self, settings: TopicEngineSettings) -> None:
        self.version = settings.combiner_version
        self._lexical_weight = settings.lexical_weight
        self._semantic_weight = settings.semantic_weight

    def combine(self, lexical: float, semantic: float | None, features: dict[str, float]) -> float:
        return 1.0 - (1.0 - self._lexical_weight * lexical) * (
            1.0 - self._semantic_weight * (semantic or 0.0)
        )


class LogisticCombiner:
    """coefficients fitted from labels by calibration.fit; features follow plan 05 section 5.4.4."""

    def __init__(self, settings: TopicEngineSettings) -> None:
        if settings.logistic_coefficients is None or len(settings.logistic_coefficients) != 7:
            raise ValueError("the logistic combiner needs exactly 7 fitted coefficients")
        self.version = settings.combiner_version
        self._beta = settings.logistic_coefficients

    def combine(self, lexical: float, semantic: float | None, features: dict[str, float]) -> float:
        s = semantic or 0.0
        values = [
            1.0,
            lexical,
            s,
            lexical * s,
            features["title_hit"],
            features["tags_only"],
            features["quality_low"],
        ]
        z = sum(beta * value for beta, value in zip(self._beta, values, strict=True))
        return 1.0 / (1.0 + math.exp(-z))


def build_combiner(settings: TopicEngineSettings) -> ScoreCombiner:
    return LogisticCombiner(settings) if settings.logistic_coefficients else NoisyOrCombiner(settings)


@dataclass
class PairState:
    video_id: str
    ref: TopicRef
    lexical: MatcherResult | None
    semantic: MatcherResult | None
    lexical_score: float
    semantic_score: float | None
    margin: float | None
    combined: float
    band: Band
    method: MatchMethod
    decision: Decision = "pending"
    confidence: Confidence = "low"
    forced: list[ReviewReason] = field(default_factory=list)
    review: list[ReviewReason] = field(default_factory=list)
    degraded: list[str] = field(default_factory=list)
    llm: dict[str, Any] | None = None
    implied_from: str | None = None
    extra_evidence: list[MatchEvidence] = field(default_factory=list)

    @property
    def lexical_flags(self) -> frozenset[str]:
        return self.lexical.flags if self.lexical else frozenset()

    @property
    def evidence(self) -> list[MatchEvidence]:
        items = list(self.lexical.evidence) if self.lexical else []
        items += list(self.semantic.evidence) if self.semantic else []
        return items + self.extra_evidence

    def add_review(self, *reasons: ReviewReason) -> None:
        for reason in reasons:
            if reason not in self.review:
                self.review.append(reason)


class DecisionPolicy:
    """bands, forced adjudication rules and confidence labels (plan 05 section 5.4.4)."""

    def __init__(self, settings: TopicEngineSettings, combiner: ScoreCombiner) -> None:
        self._settings = settings
        self._combiner = combiner

    def evaluate(
        self,
        video: PreparedVideoText,
        ref: TopicRef,
        lexical: MatcherResult | None,
        semantic: MatcherResult | None,
        semantic_missing: str | None,
    ) -> PairState:
        settings = self._settings
        lexical_score = lexical.score if lexical else 0.0
        semantic_score = semantic.score if semantic else (None if semantic_missing else 0.0)
        margin = float(semantic.detail["margin"]) if semantic else None
        flags = lexical.flags if lexical else frozenset()
        features = {
            "title_hit": float("title_hit" in flags),
            "tags_only": float("tags_only" in flags),
            "quality_low": float(
                bool({"very_short", "no_topical_text", "emoji_only_title"} & set(video.quality_flags))
            ),
        }
        combined = min(1.0, max(0.0, self._combiner.combine(lexical_score, semantic_score, features)))
        if lexical_score > 0 and (semantic_score or 0.0) >= settings.high_min_semantic:
            method = MatchMethod.lexical_semantic
        elif lexical_score > 0:
            method = MatchMethod.lexical
        else:
            method = MatchMethod.semantic
        state = PairState(
            video_id=video.video_id,
            ref=ref,
            lexical=lexical,
            semantic=semantic,
            lexical_score=lexical_score,
            semantic_score=semantic_score,
            margin=margin,
            combined=combined,
            band=self._band(combined),
            method=method,
        )
        if semantic_missing == "no_embedder":
            state.degraded.append("no_embedder")
            state.add_review(ReviewReason.degraded_mode)
        if semantic_missing == "unsupported_script":
            state.add_review(ReviewReason.unsupported_script)
        if state.band != "reject":
            state.forced = self._forced(state, video, flags)
            state.add_review(*state.forced)
        self._initial_decision(state)
        return state

    def _band(self, combined: float) -> Band:
        if combined >= self._settings.accept:
            return "accept"
        if combined < self._settings.reject:
            return "reject"
        return "ambiguous"

    def _forced(
        self, state: PairState, video: PreparedVideoText, flags: frozenset[str]
    ) -> list[ReviewReason]:
        settings = self._settings
        reasons: list[ReviewReason] = []
        semantic = state.semantic_score
        if state.lexical_score == 0 and (semantic or 0.0) < settings.semantic_only_forced_below:
            reasons.append(ReviewReason.semantic_only)
        if flags & {"ambiguous_only", "negative_context", "weak_only", "list_mention"}:
            reasons.append(ReviewReason.ambiguous_alias)
        if "all_negated" in flags:
            reasons.append(ReviewReason.negated_evidence)
        if "tags_only" in flags:
            reasons.append(ReviewReason.tags_only)
        # embeddings are known to miss hinglish and cross-script forms, so those hits are not a conflict
        conflict = state.margin is not None and state.margin < 0 and "cross_lingual_only" not in flags
        if state.lexical_score >= settings.conflict_min_lexical and conflict:
            reasons.append(ReviewReason.method_conflict)
        semantic_band = state.semantic.detail.get("band") if state.semantic else None
        if "proposed_only" in flags and semantic_band in {None, "weak"}:
            reasons.append(ReviewReason.proposed_alias_only)
        if "unsupported_script" in video.quality_flags:
            reasons.append(ReviewReason.unsupported_script)
        if "no_topical_text" in video.quality_flags:
            reasons.append(ReviewReason.low_text_quality)
        return reasons

    def _initial_decision(self, state: PairState) -> None:
        settings = self._settings
        if state.band == "reject":
            state.decision = "rejected"
            return
        needs_lexical = (
            "no_embedder" in state.degraded and state.lexical_score < settings.no_embedder_min_lexical
        )
        if state.band == "accept" and not state.forced and not needs_lexical:
            state.decision = "accepted"
            state.confidence = self._accepted_confidence(state)
            return
        state.decision = "pending"

    def _accepted_confidence(self, state: PairState) -> Confidence:
        settings = self._settings
        flags = state.lexical_flags
        high = (
            state.combined >= settings.high
            and "clean_headline_hit" in flags
            and "proposed_only" not in flags
            and state.lexical_score >= settings.high_min_lexical
            and (state.semantic_score or 0.0) >= settings.high_min_semantic
            and not state.degraded
        )
        return "high" if high else "medium"

    def force(self, state: PairState, reason: ReviewReason) -> None:
        if reason not in state.forced:
            state.forced.append(reason)
        state.add_review(reason)
        state.decision = "pending"

    def without_llm(self, state: PairState, degraded: str) -> None:
        # forced cases stand only when the score is high and nothing conflicts; everything else waits
        state.degraded.append(degraded)
        state.add_review(ReviewReason.degraded_mode)
        if degraded == "llm_budget":
            state.add_review(ReviewReason.unadjudicated_budget)
        conflict = bool(CONFLICT_REASONS & set(state.forced))
        if state.forced and state.combined >= self._settings.high and not conflict:
            state.decision = "accepted"
            state.confidence = "low"
            return
        state.decision = "unadjudicated"
        state.confidence = "low"

    def llm_accepted(self, state: PairState, quote: str, field_name: str, detail: dict[str, Any]) -> None:
        state.decision = "accepted"
        state.method = MatchMethod.llm_adjudicated
        state.combined = max(state.combined, self._settings.accept)
        state.confidence = "medium" if field_name in {"title", "chapters", "description"} else "low"
        if "no_embedder" in state.degraded:
            state.confidence = "low"
        state.llm = {**detail, "verdict": "match", "quote": quote, "field": field_name, "verified": True}
        state.add_review(ReviewReason.llm_decided)
        state.extra_evidence.append(
            MatchEvidence(
                method="llm",
                field="description_head" if field_name == "description" else field_name,
                span_text=quote,
                match_quality=1.0,
                detail={key: detail[key] for key in ("model", "prompt_version") if key in detail}
                | {"verified": True},
            )
        )

    def llm_rejected(
        self, state: PairState, detail: dict[str, Any], *, unverified_quote: str | None = None
    ) -> None:
        state.decision = "rejected"
        if unverified_quote is not None:
            state.add_review(ReviewReason.llm_quote_unverified)
            state.llm = {**detail, "verdict": "match", "quote": unverified_quote, "verified": False}
        else:
            state.llm = {**detail, "verdict": "none"}
