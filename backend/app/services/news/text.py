import re
import unicodedata
from difflib import SequenceMatcher

DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−"), "-")
QUOTES = {ord(c): "'" for c in "‘’‚‛′"} | {ord(c): '"' for c in "“”„″"}
WORD_PATTERN = re.compile(r"[\w.+-]+", re.UNICODE)
SPACE_PATTERN = re.compile(r"\s+")


def normalize(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text).translate(DASHES).translate(QUOTES).casefold()
    return SPACE_PATTERN.sub(" ", folded).strip()


def tokens(text: str) -> list[str]:
    return [token.strip(".-+") for token in WORD_PATTERN.findall(normalize(text)) if token.strip(".-+")]


def contains_phrase(haystack: str, needle: str) -> bool:
    needle_norm = normalize(needle)
    if not needle_norm:
        return False
    pattern = r"(?<![\w])" + re.escape(needle_norm) + r"(?![\w])"
    return re.search(pattern, normalize(haystack)) is not None


def _ratio(a: str, b: str) -> float:
    if not a and not b:
        return 100.0
    return SequenceMatcher(None, a, b).ratio() * 100


def token_set_ratio(a: str, b: str) -> float:
    # same contract as rapidfuzz.fuzz.token_set_ratio, on a 0..100 scale
    set_a, set_b = set(tokens(a)), set(tokens(b))
    if not set_a or not set_b:
        return 0.0
    common = " ".join(sorted(set_a & set_b))
    rest_a = " ".join(sorted(set_a - set_b))
    rest_b = " ".join(sorted(set_b - set_a))
    combined_a = f"{common} {rest_a}".strip()
    combined_b = f"{common} {rest_b}".strip()
    if common and (not rest_a or not rest_b):
        return 100.0
    return max(_ratio(common, combined_a), _ratio(common, combined_b), _ratio(combined_a, combined_b))


def partial_ratio(needle: str, haystack: str) -> float:
    short, long = normalize(needle), normalize(haystack)
    if not short or not long:
        return 0.0
    if len(short) > len(long):
        short, long = long, short
    width = len(short)
    best = 0.0
    for start in range(0, len(long) - width + 1):
        best = max(best, _ratio(short, long[start : start + width]))
        if best == 100.0:
            break
    return best
