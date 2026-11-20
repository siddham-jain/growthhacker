import re
from typing import Literal

from app.services.website_intel.extract.cta import ANCHOR_LEXICON, APP_STORE_PREFIXES, CtaType
from app.services.website_intel.profile.canonical import canonical_tokens, stem
from app.services.website_intel.profile.market import CITIES, COUNTRY_NAMES, REGIONS

NonOffering = Literal["call_to_action", "app_store", "navigation_label", "category_label"]

# a label that starts with an action verb asks the visitor to do something; it is not something sold
CTA_START = re.compile(
    r"^\s*(?:request|book|schedule|get|download|install|call|contact|enquire|send|submit|find|view|explore|"
    r"know|learn|read|click|sign|register|log|login|subscribe|apply|buy|shop|order|pay|chat|whatsapp|watch|"
    r"try|start|join|share|talk|speak|ask|compare|claim|upload|search|locate|visit|see|browse)\b",
    re.IGNORECASE,
)
APP_STORE = re.compile(r"\b(?:app\s*store|google\s*play|play\s*store)\b", re.IGNORECASE)
# section headings that only group offerings ("Treatments", "Our Specialities", "Centres of Excellence")
NAV_LABEL_WORDS = (
    "treatment|service|specialty|department|procedure|condition|disease|facility|product|solution|feature|"
    "offering|program|package|center|excellence|expertise|overview|home|about|u|more|all|other|our|the|of|"
    "and|view|list|menu"
)
NAV_WORDS = frozenset(NAV_LABEL_WORDS.split("|"))
# words that group offerings ("by use case", "pan-india") rather than name one
CATEGORY_WORDS = frozenset(
    {"by", "use", "case", "type", "industry", "category", "range", "collection", "pan", "wide"}
)
PLACE_WORDS = frozenset(word for name in (*REGIONS, *CITIES, *COUNTRY_NAMES) for word in name.split())


def business_words(niche: str) -> frozenset[str]:
    """the nouns of the business's own niche ("corrugated packaging manufacturer"): alone they name no offering."""
    return frozenset(stem(token) for token in canonical_tokens(niche))


def non_offering_reason(
    name: str, url: str | None, brands: frozenset[str] = frozenset(), niche: frozenset[str] = frozenset()
) -> NonOffering | None:
    """why a reduce-step offering is a call to action, an app-store link or a bare navigation heading."""
    target = (url or "").split("://", 1)[-1].lower().removeprefix("www.")
    if APP_STORE.search(name) or target.startswith(APP_STORE_PREFIXES):
        return "app_store"
    if CTA_START.search(name) or any(pattern.search(name) for pattern, _ in ANCHOR_LEXICON):
        return "call_to_action"
    tokens = [stem(token) for token in canonical_tokens(name) if token not in brands]
    if tokens and all(token in NAV_WORDS for token in tokens):
        return "navigation_label"
    # "Packaging Solutions", "Pan-India Packaging", "Packaging by Use Case" for a packaging business
    specific = [token for token in tokens if token not in NAV_WORDS | CATEGORY_WORDS | PLACE_WORDS]
    if len(specific) == 1 and len(tokens) > 1 and specific[0] in niche:
        return "category_label"
    return None


def cta_type(name: str) -> CtaType | None:
    return next((kind for pattern, kind in ANCHOR_LEXICON if pattern.search(name)), None)
