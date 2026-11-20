from app.domain.card_copy import Violation
from app.services.llm.writer.evidence_pack import BuiltPack
from app.services.llm.writer.schemas import CardCopyLLM
from app.services.llm.writer.text_match import contains_phrase, partial_ratio, ratio, token_sort_ratio, tokens
from app.services.llm.writer.validators.base import grounding_validators

# thresholds follow 07's calibrated no-copying gate (99 C22); token_set is deliberately not used
RATIO_THRESHOLD = 85
TOKEN_SORT_THRESHOLD = 88
PARTIAL_THRESHOLD = 95
MIN_CONTAINED_TOKENS = 4


def copies_title(text: str, title: str) -> bool:
    if ratio(" ".join(tokens(text)), " ".join(tokens(title))) >= RATIO_THRESHOLD:
        return True
    if token_sort_ratio(text, title) >= TOKEN_SORT_THRESHOLD:
        return True
    if len(tokens(title)) < MIN_CONTAINED_TOKENS:
        return False
    return contains_phrase(text, title) or partial_ratio(title, text) >= PARTIAL_THRESHOLD


class CopyGuard:
    """V5: the summary and working title never copy or lightly reword a pack or recent client title."""

    name = "copy_guard"

    def validate(self, copy: CardCopyLLM, built: BuiltPack) -> list[Violation]:
        violations = []
        fields = [("summary", copy.summary)]
        if copy.working_title:
            fields.append(("working_title", copy.working_title))
        for field, text in fields:
            if any(copies_title(text, title) for title in built.grounding.forbidden_titles):
                violations.append(
                    Violation(
                        rule="copies_title",
                        field=field,
                        detail="too close to an existing title; write an original angle",
                    )
                )
        return violations


grounding_validators.add("copy_guard", lambda settings: CopyGuard())
