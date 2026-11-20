import hashlib
import json

from app.domain.recommendations import (
    CreatorSubject,
    NewsSubject,
    PatternSpec,
    RecommendationDraft,
    RefreshSubject,
    VideoIdeaSubject,
)


def feedback_group(draft: RecommendationDraft) -> str:
    # the topic or pattern a user's feedback should carry to sibling cards
    subject = draft.subject
    if isinstance(subject, VideoIdeaSubject):
        return f"topic:{subject.topic_id}"
    if isinstance(subject, PatternSpec):
        traits = ",".join(f"{key}={value}" for key, value in sorted(subject.traits.items()))
        return f"{subject.kind}:{traits}"
    if isinstance(subject, CreatorSubject):
        return f"creator:{subject.channel_id}"
    if isinstance(subject, NewsSubject):
        return f"news:{subject.news_signal_id}"
    assert isinstance(subject, RefreshSubject)
    return f"refresh:{subject.video_id}"


def evidence_hash(draft: RecommendationDraft) -> str:
    # changes when the references or numbers a user sees change, which flips a card to "updated"
    payload = {
        "refs": [ref.video_id for ref in draft.references],
        "ratios": [ref.outlier_ratio for ref in draft.references],
        "reasons": [reason.params for reason in draft.reasons],
        "range": draft.expected_range.display if draft.expected_range else None,
        "confidence": draft.confidence.model_dump(mode="json") if draft.confidence else None,
    }
    return hashlib.sha1(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()
