import asyncio
import hashlib
import math
import re

from app.core.config import CardWriterSettings
from app.domain.card_copy import CardCritique, CritiqueIssue, CritiqueScores, FieldCheck
from app.ports.llm import (
    CallContext,
    LLMBudgetExceeded,
    LLMClient,
    LLMUnavailable,
    PayloadClass,
    PromptInput,
    TaskId,
    TrustedBlock,
    UntrustedBlock,
)
from app.services.llm.writer.evidence_pack import BuiltPack, stable_json
from app.services.llm.writer.schemas import CardReviewLLM, CritiqueBatchLLM
from app.services.llm.writer.specs import CARD_CRITIQUE_PROMPT
from app.services.llm.writer.writer import STAGE, WriteRequest, WriterOutcome

PASS_MIN_SCORE = 3
NOT_OBVIOUS_LOW = 2
ALIAS = re.compile(r"\bf\d{1,2}\b")


def verdict(scores: CritiqueScores, faithful: list[FieldCheck]) -> str:
    # code decides, not the model; low not_obvious is a ranking signal for 07, wording cannot fix it
    core = (scores.specific, scores.evidenced, scores.relevant, scores.actionable)
    if not all(check.ok for check in faithful) or min(core) < PASS_MIN_SCORE:
        return "revise"
    return "pass"


def critic_flags(critique: CardCritique | None) -> list[str]:
    if critique is None:
        return []
    return ["not_obvious_low"] if critique.scores.not_obvious <= NOT_OBVIOUS_LOW else []


def review_payload(outcome: WriterOutcome, built: BuiltPack) -> str:
    pack = built.pack
    return stable_json(
        {
            "copy": outcome.llm_copy.model_dump(mode="json") if outcome.llm_copy else None,
            "facts": [
                {
                    "alias": fact.alias,
                    "statement": fact.reason.statement,
                    "numbers": [number.display for number in fact.reason.numbers],
                }
                for fact in pack.facts
            ],
            "buyer_questions": [item.text for item in pack.client.buyer_questions],
        }
    )


class CardCritic:
    """haiku rubric over batches of up to five cards; never adds facts, at most one rewrite downstream."""

    def __init__(self, llm: LLMClient, settings: CardWriterSettings) -> None:
        self._llm = llm
        self._batch_size = settings.critic_batch_size

    def _input(self, items: list[tuple[BuiltPack, WriterOutcome]]) -> PromptInput:
        trusted = [
            TrustedBlock(label=f"card_c{index}", text=review_payload(outcome, built))
            for index, (built, outcome) in enumerate(items, 1)
        ]
        recent = {f"c{index}": built.grounding.forbidden_titles for index, (built, _) in enumerate(items, 1)}
        return PromptInput(
            trusted=trusted,
            untrusted=[
                UntrustedBlock(
                    label="client_recent_titles", source="youtube_videos", text=stable_json(recent)
                )
            ],
        )

    def _critique(self, review: CardReviewLLM, built: BuiltPack) -> CardCritique:
        known = set(built.grounding.statements)
        issues = [
            CritiqueIssue(
                field=issue.field,
                problem=issue.problem,
                # hints may only point at facts that exist in this pack
                fix_hint=issue.fix_hint if set(ALIAS.findall(issue.fix_hint)) <= known else "",
            )
            for issue in review.issues
        ]
        scores = CritiqueScores(**review.scores.model_dump())
        faithful = [FieldCheck(field=check.field, ok=check.ok) for check in review.faithful]
        return CardCritique(
            scores=scores,
            faithful=faithful,
            generic_phrases=review.generic_phrases,
            issues=issues,
            verdict=verdict(scores, faithful),
        )

    async def review(
        self, items: list[tuple[BuiltPack, WriterOutcome]], request: WriteRequest
    ) -> list[CardCritique | None]:
        # batches run concurrently and are balanced, so the slowest one reviews as few cards as possible
        count = math.ceil(len(items) / self._batch_size)
        size = math.ceil(len(items) / count) if count else 0
        batches = [items[start : start + size] for start in range(0, len(items), size)] if size else []
        reviewed = await asyncio.gather(*(self._review_batch(batch, request) for batch in batches))
        return [critique for batch in reviewed for critique in batch]

    async def _review_batch(
        self, batch: list[tuple[BuiltPack, WriterOutcome]], request: WriteRequest
    ) -> list[CardCritique | None]:
        prompt_input = self._input(batch)
        payload: set[PayloadClass] = set().union(*(built.payload_classes for built, _ in batch))
        context = CallContext(
            run_id=request.run_id,
            client_id=request.client_id,
            stage=STAGE,
            deadline=request.deadline,
            payload_classes=frozenset(payload),
            idempotency_key=hashlib.sha256(prompt_input.model_dump_json().encode("utf-8")).hexdigest(),
            language=batch[0][0].pack.language,
        )
        try:
            result = await self._llm.generate_structured(
                task=TaskId.card_critique,
                schema=CritiqueBatchLLM,
                system=CARD_CRITIQUE_PROMPT,
                input=prompt_input,
                context=context,
            )
        except (LLMUnavailable, LLMBudgetExceeded):
            return [None] * len(batch)
        by_alias = {review.card: review for review in result.value.reviews}
        return [
            self._critique(by_alias[f"c{index}"], built) if f"c{index}" in by_alias else None
            for index, (built, _) in enumerate(batch, 1)
        ]
