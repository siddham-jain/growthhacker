from app.core.logging import get_logger
from app.domain.card_copy import CardCopy, Violation
from app.domain.recommendations import (
    FIT_TO_CLIENT_MAX_CHARS,
    SUMMARY_MAX_CHARS,
    WHY_IT_WORKS_ITEM_MAX_CHARS,
    WHY_IT_WORKS_MAX_ITEMS,
    RecType,
)
from app.services.llm.writer.evidence_pack import BuiltPack
from app.services.llm.writer.schemas import CardCopyLLM, ReasonLine

logger = get_logger(__name__)
TEMPLATE_PROMPT_ID = "card_template"
TEMPLATE_VERSION = "template-v1"
TEMPLATE_LANGUAGE = "en"
SUMMARIES: dict[RecType, str] = {
    RecType.video_idea: "Make a video on {topic}",
    RecType.title_pattern: "Use the title pattern behind recent outliers on {topic}",
    RecType.thumbnail_pattern: "Use the thumbnail pattern behind recent outliers on {topic}",
    RecType.format: "Try the format behind recent outliers on {topic}",
    RecType.creator: "Consider a collaboration with {entity}",
    RecType.news_opportunity: "Cover the latest news on {entity} for your audience",
    RecType.description_refresh: "Refresh this video's description with your approved keywords",
}
QUESTION_SUMMARY = 'Make a video answering "{question}"'


def clip(text: str, limit: int) -> str:
    # word-boundary cut keeps numbers and names whole
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rsplit(" ", 1)[0].rstrip(",;:-")
    return f"{cut}…"


class TemplateCardWriter:
    """deterministic copy from 07's statements; the pipeline never waits on, or fails because of, the llm."""

    def draft(self, built: BuiltPack) -> CardCopyLLM:
        pack = built.pack
        grounding = built.grounding
        question = next(
            (
                item.text
                for item in pack.client.buyer_questions
                if item.alias == pack.topic.buyer_question_alias
            ),
            None,
        )
        topic = pack.topic.label or "your niche"
        entity = pack.entities[0] if pack.entities else topic
        summary = SUMMARIES[pack.rec_type].format(topic=topic, entity=entity)
        if pack.rec_type is RecType.video_idea and question:
            candidate = QUESTION_SUMMARY.format(question=question)
            summary = candidate if len(candidate) <= SUMMARY_MAX_CHARS else summary
        why_aliases = list(grounding.why_aliases) or list(grounding.statements)
        fit_aliases = list(grounding.fit_aliases)
        why_lines = [
            ReasonLine(fact_refs=[alias], text=clip(grounding.statements[alias], WHY_IT_WORKS_ITEM_MAX_CHARS))
            for alias in why_aliases[:WHY_IT_WORKS_MAX_ITEMS]
        ]
        if fit_aliases:
            fit_alias = fit_aliases[0]
            fit_text = grounding.statements[fit_alias]
        else:
            fit_alias = why_aliases[0]
            fit_text = (
                f'Answers a question your buyers ask: "{question}"'
                if question
                else f"Fits your focus on {topic}"
            )
        return CardCopyLLM(
            summary=clip(summary, SUMMARY_MAX_CHARS),
            working_title=None,
            why_it_works=why_lines,
            fit_to_client=ReasonLine(fact_refs=[fit_alias], text=clip(fit_text, FIT_TO_CLIENT_MAX_CHARS)),
        )

    def write(
        self,
        built: BuiltPack,
        *,
        degrade_reason: str | None,
        violations: list[Violation] | None = None,
        attempts: int = 0,
    ) -> CardCopy:
        if not built.grounding.statements:
            raise ValueError(f"draft {built.pack.draft_id} has no facts; the writer needs at least one")
        if built.pack.language.split("-")[0] != TEMPLATE_LANGUAGE:
            logger.info("card_template_language_fallback", language=built.pack.language)
        return to_card_copy(
            self.draft(built),
            built,
            copy_source="template",
            prompt_id=TEMPLATE_PROMPT_ID,
            prompt_version=TEMPLATE_VERSION,
            model=None,
            attempts=attempts,
            violations=violations or [],
            degrade_reason=degrade_reason,
            language=TEMPLATE_LANGUAGE,
        )


def to_card_copy(
    copy: CardCopyLLM,
    built: BuiltPack,
    *,
    copy_source: str,
    prompt_id: str,
    prompt_version: str,
    model: str | None,
    attempts: int,
    violations: list[Violation],
    degrade_reason: str | None = None,
    language: str | None = None,
) -> CardCopy:
    reason_ids = built.grounding.reason_ids
    keeps_title = built.pack.rec_type in {RecType.video_idea, RecType.news_opportunity}
    return CardCopy.model_validate(
        {
            "draft_id": built.pack.draft_id,
            "summary": copy.summary,
            "working_title": copy.working_title if keeps_title else None,
            "why_it_works": [line.text for line in copy.why_it_works],
            "fit_to_client": copy.fit_to_client.text,
            "reason_fact_ids": [[reason_ids[ref] for ref in line.fact_refs] for line in copy.why_it_works],
            "fit_fact_ids": [reason_ids[ref] for ref in copy.fit_to_client.fact_refs],
            "copy_source": copy_source,
            "degrade_reason": degrade_reason,
            "prompt_id": prompt_id,
            "prompt_version": prompt_version,
            "model": model,
            "attempts": attempts,
            "violations_last": [violation.model_dump() for violation in violations],
            "language": language or built.pack.language,
        }
    )
