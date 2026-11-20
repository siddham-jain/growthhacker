from collections.abc import Iterable

from app.core.registry import trait_extractor_registry
from app.domain.analytics import TopicTrait
from app.domain.topics import AnswerDirectness, TopicMatch
from app.domain.youtube import VideoRecord
from app.services.analytics.traits.base import Features, TraitContext


def topic_traits(
    matches: Iterable[TopicMatch], directness: Iterable[AnswerDirectness], min_score: float
) -> tuple[TopicTrait, ...]:
    # 05 owns matching and directness; only accepted matches at or above the score floor become traits
    levels = {(d.video_id, d.buyer_question_ref_id): d.level for d in directness}
    traits = [
        TopicTrait(
            topic_ref_id=m.topic_ref_id,
            kind=m.topic_kind.value,
            score=m.combined_score,
            review_flag=m.review_flag,
            directness=levels.get((m.video_id, m.topic_ref_id)),
        )
        for m in matches
        if m.accepted and m.combined_score >= min_score
    ]
    return tuple(sorted(traits, key=lambda t: (-t.score, t.topic_ref_id)))


@trait_extractor_registry.register("topic")
class TopicTraitExtractor:
    key = "topic"
    scope = "client_context"
    version = "topic-1"

    def extract(self, video: VideoRecord, ctx: TraitContext) -> tuple[TopicTrait, ...]:
        return ctx.topics

    def mined(self, part: tuple[TopicTrait, ...]) -> Features:
        return {"topic": frozenset(t.topic_ref_id for t in part)}
