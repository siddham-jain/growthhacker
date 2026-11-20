from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from app.domain.common import DomainModel, VideoId
from app.domain.topics import MatchEvidence, MatchMethod, TopicMatch, TopicRefId

OverrideVerdict = Literal["accept", "reject", "reassign"]


class TopicMatchOverride(DomainModel):
    # a reviewer can only pick among the client's existing topics or none, never a new label
    client_id: UUID
    video_id: VideoId
    topic_ref_id: TopicRefId
    verdict: OverrideVerdict
    reassigned_to_ref_id: TopicRefId | None = None
    reviewer_id: UUID | None = None
    reason: str | None = None


@dataclass(frozen=True)
class OverrideRule:
    topic_ref_id: str
    verdict: OverrideVerdict
    reassigned_to_ref_id: str | None = None
    reviewer_id: UUID | None = None


class ReviewItemView(DomainModel):
    video_id: VideoId
    topic_ref_id: TopicRefId
    ref_label: str
    reasons: list[str]
    priority: float
    match: TopicMatch | None


def overridden(match: TopicMatch, accept: bool, at: datetime, reviewer: UUID | None) -> TopicMatch:
    evidence = [
        *match.evidence,
        MatchEvidence(
            method="override",
            field=None,
            match_quality=1.0,
            detail={
                "verdict": "accept" if accept else "reject",
                "reviewer_id": str(reviewer) if reviewer else None,
            },
        ),
    ]
    return match.model_copy(
        update={
            "decision": "accepted" if accept else "rejected",
            "method": MatchMethod.override,
            "confidence": "high",
            "combined_score": max(match.combined_score, 0.85) if accept else match.combined_score,
            "evidence": evidence,
            "review_reasons": [],
            "matched_at": at,
        }
    )


def apply_rules(
    matches: list[TopicMatch],
    rules: Sequence[OverrideRule],
    known_refs: Collection[str],
    blank: Callable[[str], TopicMatch],
    at: datetime,
) -> list[TopicMatch]:
    """reviewer decisions win over any recompute until the video text changes."""
    by_ref = {match.topic_ref_id: match for match in matches}
    for rule in rules:
        targets = [(rule.topic_ref_id, rule.verdict == "accept")]
        if rule.verdict == "reassign" and rule.reassigned_to_ref_id:
            targets.append((rule.reassigned_to_ref_id, True))
        for ref_id, accept in targets:
            if ref_id not in known_refs:
                continue
            base = by_ref.get(ref_id) or blank(ref_id)
            by_ref[ref_id] = overridden(base, accept, at, rule.reviewer_id)
    return list(by_ref.values())
