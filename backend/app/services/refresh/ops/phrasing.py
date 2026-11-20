import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.core.config import RefreshSettings
from app.domain.refresh import MAX_OPERATION_CHARS
from app.ports.llm import (
    CallBudget,
    CallContext,
    LLMClient,
    LLMError,
    PayloadClass,
    PromptInput,
    PromptRef,
    TaskId,
    TrustedBlock,
    UntrustedBlock,
)
from app.services.refresh.evidence import VideoContentEvidence
from app.services.refresh.ops.templates import TemplateLine
from app.services.refresh.ops.validator import InsertionValidator
from app.services.refresh.triggers.base import Finding

PROMPT_ID = "refresh_phrasing"
MAX_OUTPUT_TOKENS = 200


class PhrasedLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(max_length=MAX_OPERATION_CHARS)


@dataclass(frozen=True, slots=True)
class PhrasingResult:
    line: TemplateLine
    phrased_by: Literal["template", "llm"]
    violations: tuple[str, ...] = ()
    llm_calls: int = 0
    fell_back: bool = False

    @property
    def accepted(self) -> bool:
        return not self.violations


class InsertionPhraser:
    """the llm may only rephrase a validated template line; code owns keywords, urls and numbers."""

    def __init__(
        self,
        validator: InsertionValidator,
        settings: RefreshSettings,
        llm: LLMClient | None = None,
        *,
        run_id: UUID | None = None,
        client_id: UUID | None = None,
    ) -> None:
        self.validator = validator
        self.settings = settings
        self.llm = llm if settings.llm_phrasing else None
        self.run_id = run_id
        self.client_id = client_id
        self._calls_left = settings.llm_max_calls
        self._semaphore = asyncio.Semaphore(settings.llm_concurrency)

    async def phrase(
        self,
        template: TemplateLine,
        *,
        finding: Finding,
        evidence: VideoContentEvidence,
        references: Sequence[str],
        avoid: Sequence[str] = (),
    ) -> PhrasingResult:
        def check(line: TemplateLine, phrased_by: str) -> list[str]:
            return self.validator.validate(
                line,
                finding=finding,
                evidence=evidence,
                phrased_by=phrased_by,
                template_text=template.text,
                references=references,
            )

        template_problems = tuple(check(template, "template"))
        calls = 0
        if self.llm is not None and self._llm_allowed(template, evidence):
            violations: list[str] = []
            for _ in range(2):
                if self._calls_left <= 0:
                    break
                self._calls_left -= 1
                calls += 1
                text = await self._ask(self.llm, template, finding, evidence, violations, avoid)
                if text is None:
                    break
                candidate = replace(template, text=text.strip())
                violations = check(candidate, "llm")
                if not violations:
                    return PhrasingResult(candidate, "llm", llm_calls=calls)
        return PhrasingResult(template, "template", template_problems, llm_calls=calls, fell_back=calls > 0)

    def _llm_allowed(self, template: TemplateLine, evidence: VideoContentEvidence) -> bool:
        return (
            self.llm is not None
            and template.rephrasable
            and evidence.language in self.settings.supported_languages
            and not evidence.code_mixed
        )

    async def _ask(
        self,
        llm: LLMClient,
        template: TemplateLine,
        finding: Finding,
        evidence: VideoContentEvidence,
        violations: Sequence[str],
        avoid: Sequence[str],
    ) -> str | None:
        allowed_terms = sorted(set(evidence.covered_tokens) | set(template.vocabulary))
        trusted = [
            TrustedBlock(label="template_line", text=template.text),
            TrustedBlock(label="required_keywords", text="\n".join(template.keywords_added)),
            TrustedBlock(label="allowed_terms", text=", ".join(allowed_terms)),
            TrustedBlock(label="claim_level", text=finding.claim_level.value if finding.claim_level else ""),
            TrustedBlock(label="max_chars", text=str(MAX_OPERATION_CHARS)),
            TrustedBlock(label="language", text=evidence.language),
        ]
        if violations:
            trusted.append(TrustedBlock(label="previous_violations", text="\n".join(violations)))
        if avoid:
            trusted.append(TrustedBlock(label="lines_used_on_other_videos", text="\n".join(avoid)))
        head = "\n".join(evidence.structure.lines[:3])
        untrusted = [UntrustedBlock(label="description_head", source="youtube_public", text=head)]
        try:
            async with self._semaphore:
                result = await llm.generate_structured(
                    task=TaskId.refresh_phrasing,
                    schema=PhrasedLine,
                    system=PromptRef(prompt_id=PROMPT_ID),
                    input=PromptInput(trusted=trusted, untrusted=untrusted),
                    context=CallContext(
                        run_id=self.run_id,
                        client_id=self.client_id,
                        stage="refresh",
                        payload_classes=frozenset(
                            {PayloadClass.public_youtube, PayloadClass.client_profile, PayloadClass.news}
                        ),
                        language=evidence.language,
                    ),
                    tier="low",
                    budget=CallBudget(max_output_tokens=MAX_OUTPUT_TOKENS, max_attempts=2),
                )
        except LLMError:
            return None
        return result.value.text
