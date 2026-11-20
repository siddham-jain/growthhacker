import re
import unicodedata
from collections import Counter
from difflib import SequenceMatcher

DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−"), "-")
QUOTES = {ord(c): "'" for c in "‘’‚‛′"} | {ord(c): '"' for c in "“”„″"}
WORD_PATTERN = re.compile(r"[\w.+-]+", re.UNICODE)
SPACE_PATTERN = re.compile(r"\s+")
# difflib junks popular characters of sequences this long, so an exact copy may score below 100
AUTOJUNK_MIN_LENGTH = 200


def normalize(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text).translate(DASHES).translate(QUOTES).casefold()
    return SPACE_PATTERN.sub(" ", folded).strip()


def tokens(text: str) -> list[str]:
    return [token.strip(".-+") for token in WORD_PATTERN.findall(normalize(text)) if token.strip(".-+")]


def contains_phrase(haystack: str, needle: str) -> bool:
    return contains_normalized(normalize(haystack), normalize(needle))


def contains_normalized(haystack: str, needle: str) -> bool:
    # both sides already normalized; callers matching many pairs normalize once
    if not needle:
        return False
    pattern = r"(?<![\w])" + re.escape(needle) + r"(?![\w])"
    return re.search(pattern, haystack) is not None


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


def partial_ratio(needle: str, haystack: str, floor: float = 0.0) -> float:
    return partial_ratio_normalized(normalize(needle), normalize(haystack), floor)


def partial_ratio_normalized(short: str, long: str, floor: float = 0.0) -> float:
    # windows that cannot beat floor are skipped, so a result at or below floor only means "no better"
    if not short or not long:
        return 0.0
    if len(short) > len(long):
        short, long = long, short
    width = len(short)
    if width < AUTOJUNK_MIN_LENGTH and short in long:
        return 100.0
    # each window's character overlap (difflib's quick_ratio) bounds its ratio: best bounds first, stop early
    need = Counter(short)
    window = Counter(long[:width])
    overlap = sum(min(count, window[char]) for char, count in need.items())
    bounds = [(overlap, 0)]
    for start in range(1, len(long) - width + 1):
        leaving, entering = long[start - 1], long[start + width - 1]
        if window[leaving] <= need[leaving]:
            overlap -= 1
        window[leaving] -= 1
        window[entering] += 1
        if window[entering] <= need[entering]:
            overlap += 1
        bounds.append((overlap, start))
    bounds.sort(key=lambda item: (-item[0], item[1]))
    best = floor
    for overlap, start in bounds:
        if 100.0 * overlap / width <= best:
            break
        best = max(best, _ratio(short, long[start : start + width]))
        if best == 100.0:
            break
    return best
