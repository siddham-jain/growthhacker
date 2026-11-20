from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

from app.domain.card_copy import CardCopy, Violation
from app.domain.recommendations import (
    FIT_TO_CLIENT_MAX_CHARS,
    SUMMARY_MAX_CHARS,
    CreatorSubject,
    NewsSubject,
    PatternSpec,
    RecommendationDraft,
    RefreshSubject,
    VideoIdeaSubject,
)
from app.services.recommend.evidence.traits import SEGMENT_LABELS, slot_template, traits_label
from app.services.recommend.inputs import ClientFacts

TEMPLATE_PROMPT_ID = "recommend.template"
TEMPLATE_VERSION = "1"
SEGMENT_SINGULAR = {"short_form": "Short", "long_form": "long-form video", "live": "live stream"}
TOPIC_MAX_CHARS = 90


@dataclass(frozen=True, slots=True)
class WriterFacts:
    # names and titles the writer may mention or must avoid; the same for every card of a window
    buyer_questions: dict[UUID, str] = field(default_factory=dict)
    topic_labels: dict[str, str] = field(default_factory=dict)
    recent_titles: tuple[str, ...] = ()
    known_channel_names: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CardRequest:
    draft: RecommendationDraft
    client: ClientFacts
    facts: WriterFacts = field(default_factory=WriterFacts)
    run_id: UUID | None = None
    violations: tuple[Violation, ...] = ()


class CardWriter(Protocol):
    # turns drafts into words; reason_fact_ids/fit_fact_ids must be the draft's reason ids. drafts missing
    # from the result fall back to the template renderer, so an adapter never needs to raise for one card
    async def write(self, requests: Sequence[CardRequest]) -> Mapping[str, CardCopy]: ...


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


class TemplateCardRenderer:
    """deterministic fallback: plainer wording, built only from the draft's structured evidence."""

    async def write(self, requests: Sequence[CardRequest]) -> Mapping[str, CardCopy]:
        return {request.draft.draft_id: self.render(request.draft, request.client) for request in requests}

    def render(self, draft: RecommendationDraft, client: ClientFacts) -> CardCopy:
        reasons = draft.reasons[:3]
        fit_parts: list[str] = []
        fit_ids: list[str] = []
        for reason in draft.fit_reasons:
            candidate = "; ".join([*fit_parts, reason.statement])
            if len(candidate) > FIT_TO_CLIENT_MAX_CHARS:
                break
            fit_parts.append(reason.statement)
            fit_ids.append(reason.reason_id)
        return CardCopy(
            draft_id=draft.draft_id,
            summary=_clip(self.summary(draft), SUMMARY_MAX_CHARS),
            why_it_works=[reason.statement for reason in reasons],
            fit_to_client="; ".join(fit_parts)
            or _clip(draft.fit_reasons[0].statement, FIT_TO_CLIENT_MAX_CHARS),
            reason_fact_ids=[[reason.reason_id] for reason in reasons],
            fit_fact_ids=fit_ids or [draft.fit_reasons[0].reason_id],
            copy_source="template",
            prompt_id=TEMPLATE_PROMPT_ID,
            prompt_version=TEMPLATE_VERSION,
            attempts=0,
            language=client.language,
        )

    @staticmethod
    def summary(draft: RecommendationDraft) -> str:
        subject = draft.subject
        if isinstance(subject, VideoIdeaSubject):
            topic = _clip(subject.topic_text.rstrip("?"), TOPIC_MAX_CHARS)
            place = " in [city]" if subject.placeholders else ""
            if subject.format_key is None:
                return f"A video on {topic}{place}"
            segment, _, band = subject.format_key.partition("|")
            return f"{topic}{place}: a {band} {SEGMENT_SINGULAR.get(segment, SEGMENT_LABELS.get(segment, segment))}"
        if isinstance(subject, PatternSpec):
            if subject.kind == "title":
                return f"Title pattern: {slot_template(subject.traits)}"
            if subject.kind == "thumbnail":
                return f"Thumbnail pattern: {traits_label(subject.traits)}"
            return f"Format: {traits_label(subject.traits)}"
        if isinstance(subject, CreatorSubject):
            return f"Work with {subject.title}"
        if isinstance(subject, NewsSubject):
            return f"News: {subject.angle}"
        assert isinstance(subject, RefreshSubject)
        keywords = sorted({keyword for op in subject.operations for keyword in op.keywords_added})
        if keywords:
            return f"Refresh an older video's description with {', '.join(keywords)}"
        return "Refresh an older video's description"
