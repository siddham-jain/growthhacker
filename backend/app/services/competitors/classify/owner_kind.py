import re

from app.domain.competitors import OwnerKind

HONORIFIC = re.compile(r"^\s*(dr|prof|mr|mrs|ms|shri|smt|sri|adv|ca)\b\.?\s", re.IGNORECASE)
ORG_TOKENS = frozenset(
    {
        "hospital",
        "hospitals",
        "clinic",
        "clinics",
        "healthcare",
        "health",
        "ltd",
        "limited",
        "pvt",
        "inc",
        "llc",
        "official",
        "media",
        "news",
        "academy",
        "institute",
        "centre",
        "center",
        "foundation",
        "group",
        "tv",
        "network",
        "labs",
        "studio",
        "studios",
        "solutions",
        "services",
        "care",
        "pharmacy",
        "university",
        "college",
        "school",
        "company",
        "global",
        "india",
    }
)
NAME_TOKEN = re.compile(r"^[A-Z][a-z]+\.?$")


def owner_kind_from_title(title: str) -> OwnerKind:
    # classifies who owns the channel, never the audience or the content category
    if HONORIFIC.match(title):
        return OwnerKind.individual
    tokens = re.findall(r"[\w.]+", title)
    if any(token.casefold().strip(".") in ORG_TOKENS for token in tokens):
        return OwnerKind.organisation
    if 2 <= len(tokens) <= 3 and all(NAME_TOKEN.match(token) for token in tokens):
        return OwnerKind.individual
    return OwnerKind.unknown
