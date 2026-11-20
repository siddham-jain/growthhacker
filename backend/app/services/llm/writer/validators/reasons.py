from itertools import combinations

from app.domain.card_copy import Violation
from app.domain.recommendations import WHY_IT_WORKS_MAX_ITEMS
from app.services.llm.writer.evidence_pack import BuiltPack
from app.services.llm.writer.schemas import CardCopyLLM
from app.services.llm.writer.text_match import content_stems, token_set_ratio
from app.services.llm.writer.validators.base import grounding_validators

MIN_REASONS = 2
DUPLICATE_THRESHOLD = 90


def min_reasons(built: BuiltPack) -> int:
    eligible = built.grounding.why_aliases or tuple(built.grounding.statements)
    return max(1, min(MIN_REASONS, len(eligible)))


class ReasonGrounding:
    """V4: reasons cite real facts, say something those facts say, and do not repeat each other."""

    name = "reasons"

    def validate(self, copy: CardCopyLLM, built: BuiltPack) -> list[Violation]:
        grounding = built.grounding
        known = set(grounding.statements)
        violations = []
        lines = [(f"why_it_works_{i}", line) for i, line in enumerate(copy.why_it_works, 1)]
        lines.append(("fit_to_client", copy.fit_to_client))
        for field, line in lines:
            unknown = [ref for ref in line.fact_refs if ref not in known]
            for ref in unknown:
                violations.append(
                    Violation(
                        rule="unknown_fact_alias", field=field, token=ref, detail=f"{ref} is not in the pack"
                    )
                )
            cited = [ref for ref in line.fact_refs if ref in known]
            stems = (
                set().union(*(content_stems(grounding.statements[ref]) for ref in cited)) if cited else set()
            )
            if cited and not content_stems(line.text) & stems:
                violations.append(
                    Violation(
                        rule="reason_not_supported",
                        field=field,
                        detail="restate what the cited facts say, using their wording",
                    )
                )
        count = len(copy.why_it_works)
        if count < min_reasons(built):
            violations.append(
                Violation(
                    rule="too_few_reasons",
                    field="why_it_works",
                    detail=f"write at least {min_reasons(built)}",
                )
            )
        if count > WHY_IT_WORKS_MAX_ITEMS:
            violations.append(
                Violation(
                    rule="too_many_reasons",
                    field="why_it_works",
                    detail=f"write at most {WHY_IT_WORKS_MAX_ITEMS}",
                )
            )
        for (i, left), (j, right) in combinations(enumerate(copy.why_it_works, 1), 2):
            same_facts = set(left.fact_refs) == set(right.fact_refs)
            if same_facts or token_set_ratio(left.text, right.text) >= DUPLICATE_THRESHOLD:
                violations.append(
                    Violation(
                        rule="duplicate_reason",
                        field=f"why_it_works_{j}",
                        detail=f"repeats why_it_works_{i}; use a different fact",
                    )
                )
        fit_refs = set(copy.fit_to_client.fact_refs)
        if grounding.fit_aliases and not fit_refs & set(grounding.fit_aliases):
            violations.append(
                Violation(
                    rule="fit_without_fit_fact",
                    field="fit_to_client",
                    detail=f"cite one of the client-fit facts: {', '.join(grounding.fit_aliases)}",
                )
            )
        return violations


grounding_validators.add("reasons", lambda settings: ReasonGrounding())
