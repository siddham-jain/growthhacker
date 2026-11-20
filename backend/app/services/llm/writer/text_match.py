import re
import unicodedata
from difflib import SequenceMatcher

WORD = re.compile(r"[^\W_]+", re.UNICODE)
STOPWORDS = frozenset(
    """
    a an and are as at be been but by can for from has have how in into is it its more most of on or our
    over than that the their them they this those to up was were what when where which who why will with
    you your about after also any been before both each few just like many much only other same so some
    such then there these very while video videos channel channels views
    """.split()  # noqa: SIM905
)
SUFFIXES = ("ingly", "ings", "ing", "edly", "ed", "ies", "es", "s", "ly")


def normalize(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text).casefold()
    return " ".join(WORD.findall(folded))


def tokens(text: str) -> list[str]:
    return normalize(text).split()


def stem(word: str) -> str:
    for suffix in SUFFIXES:
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def content_stems(text: str) -> set[str]:
    return {stem(word) for word in tokens(text) if len(word) >= 4 and word not in STOPWORDS}


def ratio(left: str, right: str) -> float:
    if not left and not right:
        return 100.0
    return 100.0 * SequenceMatcher(None, left, right, autojunk=False).ratio()


def token_sort_ratio(left: str, right: str) -> float:
    return ratio(" ".join(sorted(tokens(left))), " ".join(sorted(tokens(right))))


def token_set_ratio(left: str, right: str) -> float:
    a, b = set(tokens(left)), set(tokens(right))
    if not a or not b:
        return 0.0
    common = " ".join(sorted(a & b))
    only_a = " ".join(sorted(a - b))
    only_b = " ".join(sorted(b - a))
    with_a = f"{common} {only_a}".strip()
    with_b = f"{common} {only_b}".strip()
    return (
        max(ratio(common, with_a), ratio(common, with_b), ratio(with_a, with_b))
        if common
        else ratio(with_a, with_b)
    )


def partial_ratio(short: str, long: str) -> float:
    # best alignment of the shorter string inside the longer one, in normalised word space
    short_norm, long_norm = normalize(short), normalize(long)
    if len(short_norm) > len(long_norm):
        short_norm, long_norm = long_norm, short_norm
    if not short_norm:
        return 0.0
    width = len(short_norm)
    best = 0.0
    for match in SequenceMatcher(None, short_norm, long_norm, autojunk=False).get_matching_blocks():
        start = max(0, match.b - match.a)
        window = long_norm[start : start + width]
        best = max(best, ratio(short_norm, window))
        if best == 100.0:
            break
    return best


def contains_phrase(text: str, phrase: str) -> bool:
    # whole-word containment after normalisation
    needle = normalize(phrase)
    return bool(needle) and f" {needle} " in f" {normalize(text)} "
