from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID

from app.domain.profile import BuyerQuestion, FunnelStage, ReviewStatus
from app.domain.topics import TopicKind, TopicRef

KIND_WEIGHT: dict[TopicKind, float] = {
    TopicKind.buyer_question: 1.0,
    TopicKind.keyword: 0.7,
    TopicKind.offering: 0.7,
    TopicKind.news_entity: 0.5,
}
INTENT_BY_STAGE: dict[FunnelStage, float] = {
    FunnelStage.decision: 1.0,
    FunnelStage.consideration: 0.7,
    FunnelStage.awareness: 0.4,
    FunnelStage.post_purchase: 0.4,
}
KEYWORD_INTENT = 0.3


@dataclass(frozen=True, slots=True)
class TopicInfo:
    ref: TopicRef
    buyer_question: BuyerQuestion | None

    @property
    def id(self) -> str:
        return self.ref.id

    @property
    def label(self) -> str:
        if self.buyer_question is not None:
            return self.buyer_question.text
        return self.ref.label

    @property
    def is_buyer_question(self) -> bool:
        return self.buyer_question is not None

    @property
    def weight(self) -> float:
        return KIND_WEIGHT[self.ref.kind]

    @property
    def stage(self) -> FunnelStage | None:
        if self.buyer_question is not None:
            return self.buyer_question.funnel_stage
        return self.ref.funnel_stage

    @property
    def intent(self) -> float:
        if not self.is_buyer_question or self.stage is None:
            return KEYWORD_INTENT
        return INTENT_BY_STAGE[self.stage]


class TopicCatalog:
    """the client's own topic refs; buyer questions count only while the client tracks them."""

    def __init__(
        self,
        refs: Iterable[TopicRef],
        buyer_questions: Iterable[BuyerQuestion],
        similarity: dict[tuple[str, str], float] | None = None,
    ) -> None:
        questions = {str(question.id): question for question in buyer_questions}
        self._topics: dict[str, TopicInfo] = {}
        for ref in refs:
            if ref.status != "active":
                continue
            question = None
            if ref.kind is TopicKind.buyer_question:
                question = questions.get(ref.source_item_id)
                if question is None or question.status is not ReviewStatus.confirmed:
                    continue
            self._topics[ref.id] = TopicInfo(ref=ref, buyer_question=question)
        self._by_question: dict[UUID, TopicInfo] = {
            topic.buyer_question.id: topic for topic in self._topics.values() if topic.buyer_question
        }
        self._similarity = similarity or {}

    def __contains__(self, topic_id: object) -> bool:
        return topic_id in self._topics

    def get(self, topic_id: str) -> TopicInfo | None:
        return self._topics.get(topic_id)

    def all(self) -> list[TopicInfo]:
        return sorted(self._topics.values(), key=lambda topic: topic.id)

    def ordered(self) -> list[TopicInfo]:
        # buyer questions first, highest intent first, then keywords; ids keep it deterministic
        return sorted(
            self._topics.values(), key=lambda topic: (not topic.is_buyer_question, -topic.intent, topic.id)
        )

    def buyer_questions(self) -> list[TopicInfo]:
        return [topic for topic in self.ordered() if topic.is_buyer_question]

    def for_question(self, question_id: UUID | None) -> TopicInfo | None:
        if question_id is None:
            return None
        return self._by_question.get(question_id)

    def similarity(self, topic_a: str, topic_b: str) -> float:
        if topic_a == topic_b:
            return 1.0
        return self._similarity.get((topic_a, topic_b), self._similarity.get((topic_b, topic_a), 0.0))
