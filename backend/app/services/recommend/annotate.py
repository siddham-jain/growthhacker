from app.domain.common import FormatSegment
from app.domain.recommendations import (
    CreatorSubject,
    MatchSpec,
    PatternSpec,
    RecommendationDraft,
    ReferenceVideo,
    VideoIdeaSubject,
)
from app.services.recommend.confidence import rule_for
from app.services.recommend.context import RecommendContext
from app.services.recommend.evidence.references import ReferenceSelector
from app.services.recommend.expectation import strategy_for
from app.services.recommend.lens import LensAssigner


class DraftAnnotator:
    """references, expected range, confidence, lens and match spec, in that order (each reads the previous)."""

    def __init__(self, references: ReferenceSelector, lenses: LensAssigner) -> None:
        self.references = references
        self.lenses = lenses

    def annotate(
        self, draft: RecommendationDraft, ctx: RecommendContext, refs: list[ReferenceVideo] | None = None
    ) -> RecommendationDraft:
        selected = refs if refs is not None else self.references.select(draft)
        draft = draft.model_copy(update={"references": selected})
        strategy = strategy_for(draft.type)
        if strategy is not None:
            estimate = strategy.estimate(draft, ctx)
            flags = sorted(set(draft.flags) | set(estimate.flags))
            draft = draft.model_copy(update={"expected_range": estimate.range, "flags": flags})
        rule = rule_for(draft.type)
        if rule is not None:
            draft = draft.model_copy(update={"confidence": rule.label(draft, ctx)})
        draft = draft.model_copy(update={"lenses": self.lenses.assign(draft, ctx)})
        return draft.model_copy(update={"match_spec": match_spec(draft)})


def match_spec(draft: RecommendationDraft) -> MatchSpec:
    # what a future video must look like to count as following this card (12 backtests on it)
    subject = draft.subject
    segment: FormatSegment | None = None
    length_band = None
    title: dict[str, bool | str] = {}
    thumbnail: dict[str, bool | str] = {}
    channel_id = None
    if isinstance(subject, VideoIdeaSubject):
        if subject.format_key:
            segment_value, _, length_band = subject.format_key.partition("|")
            segment = FormatSegment(segment_value)
        title = {key.removeprefix("title."): value for key, value in subject.title_hint_traits.items()}
        thumbnail = {
            key.removeprefix("thumbnail."): value for key, value in subject.thumbnail_hint_traits.items()
        }
    elif isinstance(subject, PatternSpec):
        for key, value in subject.traits.items():
            if key.startswith("title."):
                title[key.removeprefix("title.")] = value
            elif key.startswith("thumbnail."):
                thumbnail[key.removeprefix("thumbnail.")] = value
            elif key == "format_key":
                segment_value, _, length_band = value.partition("|")
                segment = FormatSegment(segment_value)
            elif key == "segment":
                segment = FormatSegment(value)
            elif key == "length_band":
                length_band = value
    elif isinstance(subject, CreatorSubject):
        channel_id = subject.channel_id
    specificity = (
        len(draft.topic_ids) + (segment is not None) + (length_band is not None) + len(title) + len(thumbnail)
    )
    return MatchSpec(
        spec_id=draft.stable_key,
        rec_type=draft.type,
        topic_ids=list(draft.topic_ids),
        format=segment,
        length_band=length_band,
        title_traits=title,
        thumbnail_traits=thumbnail,
        channel_id=channel_id,
        lens=[assignment.lens for assignment in draft.lenses],
        buyer_question_id=draft.buyer_question_id,
        specificity=specificity + (channel_id is not None),
        source="system",
    )
