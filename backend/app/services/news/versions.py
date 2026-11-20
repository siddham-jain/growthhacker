import re
from collections.abc import Iterable

from app.services.news.text import normalize

MODIFIERS = frozenset({"pro", "ultra", "mini", "max", "plus", "lite", "nano", "flash", "turbo", "se", "air"})
# title-case words that precede plain numbers without naming a product
NON_ENTITY_WORDS = frozenset(
    {
        "top", "best", "over", "under", "than", "of", "in", "for", "with", "and", "or", "to", "by", "at",
        "beats", "raises", "gets", "adds", "hits", "tops", "nears", "every", "day", "days", "week", "weeks",
        "month", "months", "year", "years", "hour", "hours", "step", "steps", "part", "chapter", "only",
        "just", "about", "around", "nearly", "almost", "up", "down", "cuts", "loses", "wins", "the", "a",
        "an", "from", "into", "after", "before", "since", "until", "covid", "phase", "stage", "grade",
    }
)  # fmt: skip
UNIT_WORDS = frozenset(
    {"million", "billion", "trillion", "crore", "lakh", "percent", "%", "x", "k", "m", "bn"}
)
TOKEN_PATTERN = re.compile(r"[A-Za-z][\w]*|v?\d+(?:\.\d+){0,2}[a-z]?|%")
YEAR = re.compile(r"^(19|20)\d{2}$")


def _is_version_number(token: str) -> bool:
    return bool(re.fullmatch(r"v?\d+(?:\.\d+){0,2}[a-z]?", token)) and not YEAR.match(token)


def _with_modifier(number: str, following: str | None) -> str:
    if following and following.casefold() in MODIFIERS:
        return f"{number.casefold()} {following.casefold()}"
    return number.casefold()


def version_tokens(text: str, entity_terms: Iterable[str] = ()) -> frozenset[str]:
    """version signatures such as '5.2', '17 pro' or 'v2' that sit next to a product or model name."""
    known = {normalize(term) for term in entity_terms}
    found: set[str] = set()
    words = TOKEN_PATTERN.findall(text.replace("-", " "))
    for index, word in enumerate(words):
        if not _is_version_number(word) or index == 0:
            continue
        previous = words[index - 1]
        following = words[index + 1] if index + 1 < len(words) else None
        if following and following.casefold() in UNIT_WORDS:
            continue
        explicit = "." in word or word.casefold().startswith("v")
        named = any(char.isupper() for char in previous) and previous.casefold() not in NON_ENTITY_WORDS
        if explicit or named or normalize(previous) in known:
            found.add(_with_modifier(word, following))
    return frozenset(found)


def versions_conflict(left: frozenset[str], right: frozenset[str]) -> bool:
    return bool(left) and bool(right) and left.isdisjoint(right)


def strip_versions(text: str) -> str:
    stripped = re.sub(r"[\s-]v?\d+(?:\.\d+){0,2}[a-z]?\b", "", text)
    return normalize(stripped)


def version_key(token: str) -> tuple[int, ...]:
    digits = re.findall(r"\d+", token)
    return tuple(int(part) for part in digits)
