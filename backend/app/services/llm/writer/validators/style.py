import re

from app.core.config import CardWriterSettings
from app.domain.card_copy import Violation
from app.domain.recommendations import FIT_TO_CLIENT_MAX_CHARS, SUMMARY_MAX_CHARS, WHY_IT_WORKS_ITEM_MAX_CHARS
from app.services.llm.writer.evidence_pack import BuiltPack
from app.services.llm.writer.schemas import CardCopyLLM
from app.services.llm.writer.text_match import contains_phrase
from app.services.llm.writer.validators.base import copy_fields, grounding_validators, masked_text

WORKING_TITLE_MAX_CHARS = 100
PROMISES = re.compile(
    r"\b(will|is going to)\s+(definitely\s+)?(get|drive|generate|bring|double|triple|increase|boost|grow|go viral)\b"
    r"|\bguarantee[sd]?\b|\bcertain(ly)? to\b",
    re.I,
)
# guardrail lexicons: no demographics, revenue, face descriptors, category labels or conversion claims
POLICY_LEXICONS: dict[str, re.Pattern[str]] = {
    "demographics": re.compile(
        r"\b(aged?\s+\d+|\d+\s*-\s*\d+\s*year[- ]olds?|millennials|gen\s*z|boomers|gender|income bracket|"
        r"demographics?|audience is mostly|mostly (men|women|male|female))\b",
        re.I,
    ),
    "monetisation": re.compile(
        r"\b(earns?|earning|earnings|revenue|cpm|rpm|monetis\w*|monetiz\w*|ad income|sponsorship money)\b",
        re.I,
    ),
    "faces": re.compile(
        r"\b(face|faces|facial|smiling|smiles?|person in (the )?thumbnail|expressions?)\b", re.I
    ),
    "category_label": re.compile(
        r"\bis an? \w+ channel\b|\b(entertainment|gaming|lifestyle) channel\b", re.I
    ),
}
CONVERSION_CLAIM = re.compile(r"\b(converts?|conversion rates?|conversions)\b", re.I)
CURRENCY_AMOUNT = re.compile(r"(₹|\$|\brs\.?|\binr\b|\busd\b)\s*\d", re.I)


class StyleAndClaims:
    """V6: length caps, banned hype phrases, promises and the policy lexicons."""

    name = "style"

    def __init__(self, settings: CardWriterSettings) -> None:
        self._banned = tuple(settings.banned_phrases)

    def validate(self, copy: CardCopyLLM, built: BuiltPack) -> list[Violation]:
        violations = self._lengths(copy)
        currency_in_pack = any(
            number.unit == "currency"
            for numbers in built.grounding.fact_numbers.values()
            for number in numbers
        )
        for field in copy_fields(copy):
            text = masked_text(field.text, built)
            for phrase in self._banned:
                if contains_phrase(text, phrase):
                    violations.append(
                        Violation(
                            rule="banned_phrase", field=field.name, token=phrase, detail="plain words only"
                        )
                    )
            if PROMISES.search(text):
                violations.append(
                    Violation(
                        rule="promise",
                        field=field.name,
                        detail="no promises; results are indicative, quote only the provided range",
                    )
                )
            for lexicon, pattern in POLICY_LEXICONS.items():
                if pattern.search(text):
                    violations.append(
                        Violation(
                            rule="policy_violation",
                            field=field.name,
                            token=lexicon,
                            detail=f"no {lexicon} claims",
                        )
                    )
            if CONVERSION_CLAIM.search(text) and not built.grounding.has_own_analytics:
                violations.append(
                    Violation(
                        rule="policy_violation",
                        field=field.name,
                        token="conversion_claim",
                        detail="conversion claims need the client's own analytics",
                    )
                )
            if CURRENCY_AMOUNT.search(text) and not currency_in_pack:
                violations.append(
                    Violation(
                        rule="policy_violation", field=field.name, token="currency", detail="no money amounts"
                    )
                )
        return violations

    @staticmethod
    def _lengths(copy: CardCopyLLM) -> list[Violation]:
        limits = [
            ("summary", copy.summary, SUMMARY_MAX_CHARS),
            ("fit_to_client", copy.fit_to_client.text, FIT_TO_CLIENT_MAX_CHARS),
        ]
        if copy.working_title:
            limits.append(("working_title", copy.working_title, WORKING_TITLE_MAX_CHARS))
        limits.extend(
            (f"why_it_works_{i}", line.text, WHY_IT_WORKS_ITEM_MAX_CHARS)
            for i, line in enumerate(copy.why_it_works, 1)
        )
        return [
            Violation(rule="too_long", field=field, detail=f"{len(text)} characters; keep it under {limit}")
            for field, text, limit in limits
            if len(text) > limit
        ]


grounding_validators.add("style", StyleAndClaims)
