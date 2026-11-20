import regex

from app.domain.refresh import ClaimLevel
from app.services.refresh.links.urls import URL

APPLICABILITY_VERBS = regex.compile(
    r"\b(covers?|covered|shows?|shown|explains?|explained|applies|apply|applied|works? with"
    r"|walks? through|demonstrates?|teach(es)?|taught)\b",
    regex.IGNORECASE,
)
DATED_CONTEXT = regex.compile(r"^\s*(update|news|related news)\s*\([^)]*\d{4}\)\s*:", regex.IGNORECASE)
RECORDED_EARLIER = regex.compile(
    r"\b(recorded|published|made|filmed)\s+(before|earlier|prior)\b", regex.IGNORECASE
)
PHONE = regex.compile(r"(?:\+?\d[\d\s\-()]{7,}\d)")


def asserted_level(text: str) -> ClaimLevel:
    """what a line claims, from navigational (where to go) up to applicability (it applies to X)."""
    if APPLICABILITY_VERBS.search(text):
        return ClaimLevel.l3_applicability
    if DATED_CONTEXT.search(text) and RECORDED_EARLIER.search(text):
        return ClaimLevel.l1_context
    if URL.search(text) or PHONE.search(text):
        return ClaimLevel.l0_navigational
    return ClaimLevel.l2_coverage
