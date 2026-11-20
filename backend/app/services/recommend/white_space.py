from app.domain.recommendations import WhiteSpaceNote
from app.services.recommend.evidence.index import EvidenceIndex
from app.services.recommend.topics import TopicCatalog


class WhiteSpaceDetector:
    """a tracked buyer question nobody answers on youtube is a note, never a card: it has no references."""

    def notes(self, catalog: TopicCatalog, widest: EvidenceIndex, window_days: int) -> list[WhiteSpaceNote]:
        notes = []
        channels = len(widest.channel_ids())
        for topic in catalog.buyer_questions():
            if widest.by_topic(topic.id):
                continue
            question = topic.buyer_question
            assert question is not None
            notes.append(
                WhiteSpaceNote(
                    buyer_question_id=question.id,
                    text=question.text,
                    window_days=window_days,
                    videos_checked=len(widest.videos),
                    channels_checked=channels,
                    widest_window_checked=widest.window_days,
                    ai_answer_citations=len(question.ai_cited_video_ids)
                    if question.ai_cited_video_ids is not None
                    else None,
                )
            )
        return notes
