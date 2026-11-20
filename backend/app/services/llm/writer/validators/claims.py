import re

from app.domain.card_copy import Violation
from app.services.llm.writer.evidence_pack import BuiltPack
from app.services.llm.writer.schemas import CardCopyLLM
from app.services.llm.writer.validators.base import (
    VerticalRule,
    copy_fields,
    masked_text,
    vertical_validators,
)

HEALTHCARE_CLAIMS = re.compile(
    r"\b(cures?|cured|best (hospital|doctor|surgeon|clinic|dentist)s?|100\s*% (success|safe)|painless|"
    r"no side[- ]effects|risk[- ]free|guaranteed results|miracle|permanent(ly)? cure)\b",
    re.I,
)
FINANCE_CLAIMS = re.compile(
    r"\b(guaranteed returns?|assured returns?|risk[- ]free|double your money|no risk)\b",
    re.I,
)


class VerticalClaims:
    """V7: regulated verticals forbid efficacy, superiority and return promises."""

    def __init__(self, name: str, pattern: re.Pattern[str], detail: str) -> None:
        self.name = name
        self._pattern = pattern
        self._detail = detail

    def validate(self, copy: CardCopyLLM, built: BuiltPack) -> list[Violation]:
        return [
            Violation(rule="regulated_claim", field=field.name, token=match.group(0), detail=self._detail)
            for field in copy_fields(copy)
            for match in [self._pattern.search(masked_text(field.text, built))]
            if match is not None
        ]


vertical_validators.add(
    "healthcare",
    VerticalRule(
        keywords=("health", "hospital", "clinic", "dental", "medical", "pharma", "doctor", "dentist"),
        factory=lambda settings: VerticalClaims(
            "healthcare_claims", HEALTHCARE_CLAIMS, "no efficacy, outcome or superiority claims"
        ),
    ),
)
vertical_validators.add(
    "finance",
    VerticalRule(
        keywords=("finance", "bank", "insurance", "invest", "loan", "wealth", "fintech"),
        factory=lambda settings: VerticalClaims("finance_claims", FINANCE_CLAIMS, "no return promises"),
    ),
)
