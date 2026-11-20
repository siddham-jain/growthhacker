import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.core.config import TopicEngineSettings
from app.domain.topics import (
    MatchEvidence,
    MatchMethod,
    PreparedVideoText,
    ReviewReason,
    TopicKind,
    TopicRef,
)
from app.services.topics.matching.combine import Confidence, PairState

ENTITY_KINDS = frozenset({TopicKind.keyword, TopicKind.offering})
ONE_NOTCH_DOWN: dict[Confidence, Confidence] = {"high": "medium", "medium": "low", "low": "low"}


@dataclass
class Assignment:
    pairs: list[PairState]
    unmatched_reason: str | None


class TopicAssigner:
    """multi-topic assignment: implied parents, caps, primary topic and unmatched reasons."""

    def __init__(self, settings: TopicEngineSettings) -> None:
        self._settings = settings

    def assign(
        self,
        video: PreparedVideoText,
        pairs: Sequence[PairState],
        refs_by_id: Mapping[str, TopicRef],
    ) -> Assignment:
        states = {pair.ref.id: pair for pair in pairs}
        for pair in states.values():
            if (
                pair.decision == "accepted"
                and "subsumed_only" in pair.lexical_flags
                and pair.method is not MatchMethod.llm_adjudicated
            ):
                pair.decision = "rejected"
        self._imply_parents(states, refs_by_id)
        accepted = sorted(
            (pair for pair in states.values() if pair.decision == "accepted"),
            key=lambda pair: (-pair.combined, pair.method is MatchMethod.implied, pair.ref.id),
        )
        cap = self._settings.max_topics_per_video
        if len(accepted) > cap:
            for pair in accepted:
                pair.add_review(ReviewReason.many_topics)
            for pair in accepted[cap:]:
                pair.decision = "rejected"
            accepted = accepted[:cap]
        for pair in accepted:
            if pair.confidence == "high" and self._qa_sample(video.video_id, pair.ref.id):
                pair.add_review(ReviewReason.qa_random_sample)
        unmatched = None if accepted else self._unmatched_reason(video, list(states.values()))
        return Assignment(pairs=list(states.values()), unmatched_reason=unmatched)

    def _imply_parents(self, states: dict[str, PairState], refs_by_id: Mapping[str, TopicRef]) -> None:
        children = sorted(
            (
                pair
                for pair in states.values()
                if pair.decision == "accepted" and pair.ref.kind is TopicKind.buyer_question
            ),
            key=lambda pair: -pair.combined,
        )
        for child in children:
            for parent_id in child.ref.parent_ids:
                parent = refs_by_id.get(parent_id)
                if parent is None:
                    continue
                current = states.get(parent_id)
                if current is not None and current.decision == "accepted":
                    continue
                implied = PairState(
                    video_id=child.video_id,
                    ref=parent,
                    lexical=current.lexical if current else None,
                    semantic=current.semantic if current else None,
                    lexical_score=current.lexical_score if current else 0.0,
                    semantic_score=current.semantic_score if current else None,
                    margin=current.margin if current else None,
                    combined=child.combined,
                    band="accept",
                    method=MatchMethod.implied,
                    decision="accepted",
                    confidence=ONE_NOTCH_DOWN[child.confidence],
                    degraded=list(child.degraded),
                    implied_from=child.ref.id,
                )
                implied.extra_evidence.append(
                    MatchEvidence(
                        method="implied",
                        field=None,
                        match_quality=round(child.combined, 4),
                        detail={"implied_from": child.ref.id},
                    )
                )
                states[parent_id] = implied

    def _unmatched_reason(self, video: PreparedVideoText, pairs: Sequence[PairState]) -> str:
        if "no_topical_text" in video.quality_flags and not pairs:
            return "no_topical_text"
        if "unsupported_script" in video.quality_flags and not pairs:
            return "unsupported_script"
        if any(pair.decision == "unadjudicated" for pair in pairs):
            return "unadjudicated"
        if any(pair.llm and pair.llm.get("verdict") == "none" for pair in pairs):
            return "adjudicated_none"
        if "no_topical_text" in video.quality_flags:
            return "no_topical_text"
        return "below_threshold"

    def _qa_sample(self, video_id: str, ref_id: str) -> bool:
        bucket = int(hashlib.sha256(f"{video_id}|{ref_id}".encode()).hexdigest()[:8], 16) % 10_000
        return bucket < self._settings.qa_sample_pct * 100
