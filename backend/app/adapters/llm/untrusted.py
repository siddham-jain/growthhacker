import re
import unicodedata

# bump when sanitising changes what the model sees, so cached results from the old rendering are not reused
SANITIZE_VERSION = "2"
# per-source caps in characters; callers that batch many items budget their own text below these
SOURCE_LIMITS: dict[str, int] = {
    "youtube_title": 200,
    "youtube_channel": 120,
    "youtube_description": 1500,
    "youtube_videos": 6000,
    "youtube_metadata": 40_000,
    "youtube": 4000,
    "website": 8000,
    "website_page": 30_000,
    "verified_facts": 200_000,
    "verified_profile": 40_000,
    "url_inventory": 40_000,
    "site_nav": 20_000,
    "cta_table": 10_000,
    "price_table": 20_000,
    "reduce_output": 10_000,
    "comment": 500,
    "news": 3000,
}
DEFAULT_LIMIT = 2000

INVISIBLE = re.compile("[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f​-‏‪-‮⁠-⁤⁦-⁩﻿]")
# line breaks carry the structure of fact tables and listings, so only runs of spaces collapse
SPACES = re.compile(r"[^\S\n]+")
BLANK_LINES = re.compile(r"\n\s*\n+")
INJECTION_MARKERS = (
    re.compile(r"ignore\s+(all|any|the)?\s*(previous|prior|above)\s+(instructions|prompts?|rules)", re.I),
    re.compile(r"\bsystem\s+prompt\b", re.I),
    re.compile(r"\byou\s+are\s+now\b", re.I),
    re.compile(r"(^|\s)(assistant|system|user)\s*:", re.I),
    re.compile(r"</?\s*(system|assistant|instructions?|untrusted_data)\b", re.I),
    re.compile(r"\b(disregard|override)\s+(your|the)\s+(instructions|rules|guidelines)", re.I),
    re.compile(r"[A-Za-z0-9+/]{200,}={0,2}"),
)


def sanitize(text: str, source: str) -> str:
    # escaping angle brackets means no content can close or fake the untrusted_data wrapper
    cleaned = unicodedata.normalize("NFKC", text)
    cleaned = INVISIBLE.sub("", cleaned)
    cleaned = cleaned.replace("<", "&lt;").replace(">", "&gt;")
    cleaned = BLANK_LINES.sub("\n\n", SPACES.sub(" ", cleaned)).strip()
    limit = SOURCE_LIMITS.get(source, DEFAULT_LIMIT)
    return cleaned[:limit]


def looks_like_injection(text: str) -> bool:
    normalized = INVISIBLE.sub("", unicodedata.normalize("NFKC", text))
    return any(pattern.search(normalized) for pattern in INJECTION_MARKERS)
