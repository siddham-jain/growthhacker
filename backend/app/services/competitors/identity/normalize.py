import re
import unicodedata
from difflib import SequenceMatcher
from urllib.parse import urlsplit

import regex
import tldextract

LEGAL_SUFFIXES = frozenset(
    {
        "ltd",
        "limited",
        "pvt",
        "private",
        "inc",
        "incorporated",
        "llc",
        "llp",
        "plc",
        "gmbh",
        "corp",
        "corporation",
        "co",
        "company",
        "enterprise",
        "enterprises",
        "group",
        "ag",
        "bv",
    }
)
GENERIC_TOKENS = frozenset(
    {
        "hospital",
        "hospitals",
        "healthcare",
        "health",
        "clinic",
        "clinics",
        "medical",
        "centre",
        "center",
        "centres",
        "centers",
        "institute",
        "the",
        "of",
        "and",
        "official",
        "channel",
        "care",
        "services",
        "service",
        "india",
    }
)
# combining marks are kept so devanagari and other indic names survive normalisation
_NON_WORD = regex.compile(r"[^\w\s\p{M}]")
_SPACES = re.compile(r"\s+")
_UNDERSCORE = re.compile(r"_+")
# bundled public suffix snapshot only; never fetched at runtime
_DOMAIN_EXTRACTOR = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)


def normalize_text(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text).casefold().replace("&", " and ")
    cleaned = _UNDERSCORE.sub(" ", _NON_WORD.sub(" ", folded))
    return _SPACES.sub(" ", cleaned).strip()


def normalize_name(name: str) -> str:
    tokens = normalize_text(name).split()
    kept = [token for token in tokens if token not in LEGAL_SUFFIXES]
    return " ".join(kept or tokens)


def distinctive_tokens(name: str) -> set[str]:
    tokens = normalize_name(name).split()
    distinctive = {token for token in tokens if token not in GENERIC_TOKENS}
    return distinctive or set(tokens)


def compact(text: str) -> str:
    return normalize_text(text).replace(" ", "")


def registrable_domain(value: str | None) -> str | None:
    if not value:
        return None
    candidate = value.strip()
    if "://" not in candidate:
        candidate = f"http://{candidate}"
    host = urlsplit(candidate).hostname
    if not host:
        return None
    extracted = _DOMAIN_EXTRACTOR(host)
    domain = extracted.top_domain_under_public_suffix
    return domain.lower() if domain else None


def domain_label(domain: str) -> str:
    return domain.split(".", 1)[0]


def _ratio(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio() * 100


def token_set_ratio(left: str, right: str) -> float:
    # same definition as the classic fuzzy token-set ratio, 0..100
    left_tokens, right_tokens = set(left.split()), set(right.split())
    if not left_tokens or not right_tokens:
        return 0.0
    shared = " ".join(sorted(left_tokens & right_tokens))
    left_rest = " ".join(sorted(left_tokens - right_tokens))
    right_rest = " ".join(sorted(right_tokens - left_tokens))
    left_full = f"{shared} {left_rest}".strip()
    right_full = f"{shared} {right_rest}".strip()
    return max(_ratio(shared, left_full), _ratio(shared, right_full), _ratio(left_full, right_full))


def compact_ratio(left: str, right: str) -> float:
    return _ratio(compact(left), compact(right))
