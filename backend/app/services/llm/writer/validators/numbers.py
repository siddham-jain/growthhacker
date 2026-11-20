from app.core.config import CardWriterSettings
from app.domain.card_copy import Violation
from app.services.llm.writer.evidence_pack import BuiltPack
from app.services.llm.writer.numbers import AllowedNumber, NumberToken, date_allowed, extract_numbers, matches
from app.services.llm.writer.schemas import CardCopyLLM
from app.services.llm.writer.validators.base import CopyField, copy_fields, grounding_validators, masked_text

FEEDBACK_DISPLAYS = 12


@grounding_validators.register("numbers")
class NumberGrounding:
    """V1: every number must be one the evidence carries; reason lines only their cited facts' numbers."""

    name = "numbers"

    def __init__(self, settings: CardWriterSettings) -> None:
        self._rel_tol = settings.number_rel_tol
        self._multiple_tol = settings.multiple_word_tol

    def _scope(self, field: CopyField, built: BuiltPack) -> tuple[AllowedNumber, ...]:
        grounding = built.grounding
        if field.fact_refs is None:
            return grounding.all_numbers
        cited = [number for ref in field.fact_refs for number in grounding.fact_numbers.get(ref, ())]
        return (*grounding.line_numbers, *cited)

    def _accepted(self, token: NumberToken, scope: tuple[AllowedNumber, ...], built: BuiltPack) -> bool:
        grounding = built.grounding
        if token.kind == "date":
            return date_allowed(token, grounding.allowed_dates)
        if token.kind == "year":
            return token.year in grounding.allowed_years or any(
                number.unit != "year" and number.value == token.value for number in scope
            )
        return any(
            matches(token, number, rel_tol=self._rel_tol, multiple_tol=self._multiple_tol) for number in scope
        )

    def validate(self, copy: CardCopyLLM, built: BuiltPack) -> list[Violation]:
        words = built.grounding.language.split("-")[0] == "en"
        violations = []
        for field in copy_fields(copy):
            scope = self._scope(field, built)
            for token in extract_numbers(masked_text(field.text, built), words=words):
                if self._accepted(token, scope, built):
                    continue
                displays = ", ".join(
                    sorted({number.display for number in scope if number.display})[:FEEDBACK_DISPLAYS]
                )
                rule = "number_not_in_cited_fact" if field.is_reason else "number_not_in_evidence"
                violations.append(
                    Violation(
                        rule=rule,
                        field=field.name,
                        token=token.text,
                        detail=f"'{token.text}' is not in the evidence for this line; allowed figures: {displays}",
                    )
                )
        return violations
