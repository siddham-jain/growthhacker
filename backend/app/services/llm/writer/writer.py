import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from app.core.config import CardWriterSettings
from app.domain.card_copy import CardCopy, CardCritique, Violation
from app.ports.llm import (
    CallContext,
    ClientContextBlock,
    LLMBudgetExceeded,
    LLMClient,
    LLMErrorKind,
    LLMResult,
    LLMUnavailable,
    PromptInput,
    TaskId,
    TrustedBlock,
    UntrustedBlock,
)
from app.services.llm.writer.evidence_pack import BuiltPack, client_payload, pack_payload
from app.services.llm.writer.schemas import CardCopyLLM
from app.services.llm.writer.specs import CARD_WRITE_PROMPT
from app.services.llm.writer.template import TemplateCardWriter, to_card_copy
from app.services.llm.writer.validators import SOFT_RULES, ValidatorSuite, blocking

STAGE = "write_cards"


@dataclass(frozen=True, slots=True)
class WriteRequest:
    run_id: UUID | None
    client_id: UUID | None
    # monotonic seconds; nothing starts after it
    deadline: float | None


@dataclass(frozen=True, slots=True)
class WriterOutcome:
    card_copy: CardCopy
    llm_copy: CardCopyLLM | None
    llm_calls: int


def degrade_reason_for(error: LLMUnavailable) -> str:
    kinds = {attempt.kind for attempt in error.attempts}
    if kinds and kinds <= {LLMErrorKind.refusal, LLMErrorKind.data_policy_blocked, LLMErrorKind.circuit_open}:
        return "refusal" if LLMErrorKind.refusal in kinds else "llm_unavailable"
    if LLMErrorKind.deadline_exceeded in kinds:
        return "deadline"
    return "llm_unavailable"


def violation_feedback(violations: list[Violation]) -> str:
    lines = [f"- [{violation.field}] {violation.detail}" for violation in violations]
    return (
        "Your previous draft broke these rules. Fix every item and keep the rest grounded in the pack. "
        "Only names and numbers that appear in the pack are allowed.\n" + "\n".join(lines)
    )


def critique_feedback(previous: CardCopyLLM, critique: CardCritique) -> str:
    issues = [
        f"- [{issue.field}] {issue.problem}" + (f" Hint: {issue.fix_hint}" if issue.fix_hint else "")
        for issue in critique.issues
    ]
    unfaithful = [check.field for check in critique.faithful if not check.ok]
    if unfaithful:
        issues.append(f"- These fields say more than their cited facts: {', '.join(unfaithful)}")
    return (
        "A reviewer flagged your draft below. Rewrite it so every line says only what its cited facts say, "
        "and make it more specific and actionable.\n"
        f"Draft: {previous.model_dump_json()}\n" + "\n".join(issues)
    )


class CardWriter:
    """llm writer with grounding validators, up to n regenerations, then the deterministic template."""

    def __init__(
        self,
        llm: LLMClient,
        settings: CardWriterSettings,
        *,
        validators: ValidatorSuite | None = None,
        template: TemplateCardWriter | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._llm = llm
        self._settings = settings
        self._validators = validators or ValidatorSuite(settings)
        self._template = template or TemplateCardWriter()
        self._monotonic = monotonic

    @property
    def template(self) -> TemplateCardWriter:
        return self._template

    def _time_left(self, request: WriteRequest) -> float | None:
        return None if request.deadline is None else request.deadline - self._monotonic()

    def _context(self, built: BuiltPack, request: WriteRequest) -> CallContext:
        return CallContext(
            run_id=request.run_id,
            client_id=request.client_id,
            stage=STAGE,
            deadline=request.deadline,
            payload_classes=built.payload_classes,
            idempotency_key=hashlib.sha256(built.fingerprint.encode("utf-8")).hexdigest(),
            language=built.pack.language,
        )

    def prompt_input(self, built: BuiltPack, feedback: str | None) -> PromptInput:
        evidence, videos = pack_payload(built.pack)
        trusted = [TrustedBlock(label="evidence_pack", text=evidence)]
        if feedback:
            trusted.append(TrustedBlock(label="previous_attempt_feedback", text=feedback))
        return PromptInput(
            client_context=ClientContextBlock(text=client_payload(built.pack)),
            trusted=trusted,
            untrusted=[UntrustedBlock(label="reference_videos", source="youtube_videos", text=videos)],
        )

    async def _generate(
        self, built: BuiltPack, request: WriteRequest, feedback: str | None
    ) -> LLMResult[CardCopyLLM]:
        return await self._llm.generate_structured(
            task=TaskId.card_write,
            schema=CardCopyLLM,
            system=CARD_WRITE_PROMPT,
            input=self.prompt_input(built, feedback),
            context=self._context(built, request),
        )

    def _validated(
        self, copy: CardCopyLLM, built: BuiltPack, *, last: bool
    ) -> tuple[CardCopyLLM, list[Violation]]:
        violations = self._validators.run(copy, built)
        hard = blocking(violations)
        if hard and last and copy.working_title and all(item.field == "working_title" for item in hard):
            # a bad working title should not cost the whole card on the final attempt
            copy = copy.model_copy(update={"working_title": None})
            violations = self._validators.run(copy, built)
        return copy, violations

    async def write(self, built: BuiltPack, request: WriteRequest) -> WriterOutcome:
        feedback: str | None = None
        last_violations: list[Violation] = []
        calls = 0
        reason = "validation_exhausted"
        attempts = 0
        for attempt in range(self._settings.max_attempts):
            remaining = self._time_left(request)
            if remaining is not None and remaining < self._settings.min_attempt_s:
                reason = "deadline"
                break
            if calls >= self._settings.max_llm_calls_per_card:
                break
            try:
                result = await self._generate(built, request, feedback)
            except LLMBudgetExceeded:
                return WriterOutcome(
                    self._template.write(
                        built, degrade_reason="budget", violations=last_violations, attempts=attempts
                    ),
                    None,
                    calls,
                )
            except LLMUnavailable as error:
                copy = self._template.write(
                    built,
                    degrade_reason=degrade_reason_for(error),
                    violations=last_violations,
                    attempts=attempts,
                )
                return WriterOutcome(copy, None, calls)
            attempts = attempt + 1
            calls += result.attempts
            last = attempt == self._settings.max_attempts - 1
            copy, violations = self._validated(result.value, built, last=last)
            hard = blocking(violations)
            if not hard:
                card = to_card_copy(
                    copy,
                    built,
                    copy_source="llm" if attempt == 0 else "llm_revised",
                    prompt_id=result.prompt_id,
                    prompt_version=result.prompt_version,
                    model=result.model,
                    attempts=attempts,
                    violations=[item for item in violations if item.rule in SOFT_RULES],
                )
                return WriterOutcome(card, copy, calls)
            last_violations = hard
            feedback = violation_feedback(hard)
        copy = self._template.write(
            built, degrade_reason=reason, violations=last_violations, attempts=attempts
        )
        return WriterOutcome(copy, None, calls)

    async def revise(
        self, built: BuiltPack, outcome: WriterOutcome, critique: CardCritique, request: WriteRequest
    ) -> WriterOutcome:
        # one critic-driven rewrite, re-validated but never re-critiqued
        kept = WriterOutcome(
            outcome.card_copy.model_copy(update={"critique": critique}), outcome.llm_copy, outcome.llm_calls
        )
        remaining = self._time_left(request)
        if outcome.llm_copy is None or (remaining is not None and remaining < self._settings.min_attempt_s):
            return kept
        if outcome.llm_calls >= self._settings.max_llm_calls_per_card:
            return kept
        try:
            result = await self._generate(built, request, critique_feedback(outcome.llm_copy, critique))
        except (LLMUnavailable, LLMBudgetExceeded):
            return kept
        copy, violations = self._validated(result.value, built, last=True)
        if blocking(violations):
            return kept
        card = to_card_copy(
            copy,
            built,
            copy_source="llm_revised",
            prompt_id=result.prompt_id,
            prompt_version=result.prompt_version,
            model=result.model,
            attempts=outcome.card_copy.attempts + 1,
            violations=[item for item in violations if item.rule in SOFT_RULES],
        )
        return WriterOutcome(
            card.model_copy(update={"critique": critique}), copy, outcome.llm_calls + result.attempts
        )
