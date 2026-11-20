from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from app.core.config import CardWriterSettings
from app.core.registry import Registry
from app.domain.card_copy import Violation
from app.services.llm.writer.evidence_pack import BuiltPack
from app.services.llm.writer.numbers import mask_spans, normalize_numbers
from app.services.llm.writer.schemas import CardCopyLLM

# warnings handed to the critic; they never force a regeneration
SOFT_RULES = frozenset({"unverified_entity"})


@dataclass(frozen=True, slots=True)
class CopyField:
    name: str
    text: str
    fact_refs: tuple[str, ...] | None

    @property
    def is_reason(self) -> bool:
        return self.fact_refs is not None


def copy_fields(copy: CardCopyLLM) -> list[CopyField]:
    fields = [CopyField("summary", copy.summary, None)]
    if copy.working_title:
        fields.append(CopyField("working_title", copy.working_title, None))
    fields.extend(
        CopyField(f"why_it_works_{index}", line.text, tuple(line.fact_refs))
        for index, line in enumerate(copy.why_it_works, 1)
    )
    fields.append(CopyField("fit_to_client", copy.fit_to_client.text, tuple(copy.fit_to_client.fact_refs)))
    return fields


def masked_text(text: str, built: BuiltPack) -> str:
    return mask_spans(normalize_numbers(text), built.grounding.masked_spans)


class GroundingValidator(Protocol):
    name: str

    def validate(self, copy: CardCopyLLM, built: BuiltPack) -> list[Violation]: ...


type ValidatorFactory = Callable[[CardWriterSettings], GroundingValidator]


@dataclass(frozen=True, slots=True)
class VerticalRule:
    keywords: tuple[str, ...]
    factory: ValidatorFactory


grounding_validators: Registry[ValidatorFactory] = Registry("grounding_validators")
# vertical-specific claim rules, selected when a keyword appears in the client's vertical
vertical_validators: Registry[VerticalRule] = Registry("vertical_validators")


class ValidatorSuite:
    def __init__(self, settings: CardWriterSettings) -> None:
        self._settings = settings
        self._common = [factory(settings) for factory in grounding_validators.all().values()]

    def _for(self, built: BuiltPack) -> list[GroundingValidator]:
        vertical = built.grounding.vertical.lower()
        extra = [
            rule.factory(self._settings)
            for rule in vertical_validators.all().values()
            if any(keyword in vertical for keyword in rule.keywords)
        ]
        return [*self._common, *extra]

    def run(self, copy: CardCopyLLM, built: BuiltPack) -> list[Violation]:
        violations: list[Violation] = []
        for validator in self._for(built):
            violations.extend(validator.validate(copy, built))
        return violations


def blocking(violations: list[Violation]) -> list[Violation]:
    return [violation for violation in violations if violation.rule not in SOFT_RULES]
