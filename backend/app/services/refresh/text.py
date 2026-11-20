import unicodedata
from collections.abc import Iterable, Sequence

import regex

TOKEN = regex.compile(r"[\p{L}\p{M}\p{N}]+")
HASHTAG = regex.compile(r"#([\p{L}\p{N}_]+)")
CAMEL_BOUNDARY = regex.compile(r"(?<=[\p{Ll}\p{N}])(?=\p{Lu})|(?<=\p{L})(?=\p{N})|(?<=\p{N})(?=\p{L})")
NUMBER = regex.compile(r"\d+(?:[.,]\d+)*%?")
URL_LIKE = regex.compile(r"(?:https?://|www\.)\S+", regex.IGNORECASE)

STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "been",
        "but",
        "by",
        "can",
        "do",
        "does",
        "for",
        "from",
        "has",
        "have",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "our",
        "so",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "this",
        "those",
        "to",
        "us",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
        "you",
        "your",
        "yours",
        "my",
        "me",
        "he",
        "she",
        "they",
        "his",
        "her",
        "not",
        "no",
        "yes",
        "all",
        "any",
        "also",
        "about",
        "up",
        "down",
        "out",
        "just",
        "more",
        "most",
        "very",
        "here",
        "now",
        "new",
        "get",
        "got",
        "use",
        "using",
        "via",
        "vs",
    ]
)


def _strip_latin_diacritics(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    kept: list[str] = []
    for char in decomposed:
        # only marks on latin letters are dropped; indic vowel signs are part of the word
        if unicodedata.combining(char) and kept and kept[-1].isascii():
            continue
        kept.append(char)
    return unicodedata.normalize("NFC", "".join(kept))


def split_hashtag(tag: str) -> str:
    body = tag.lstrip("#").replace("_", " ")
    return CAMEL_BOUNDARY.sub(" ", body)


def expand_hashtags(text: str) -> str:
    return HASHTAG.sub(lambda match: " " + split_hashtag(match.group(1)) + " ", text)


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = _strip_latin_diacritics(text).casefold()
    return " ".join(TOKEN.findall(text))


def stem(token: str) -> str:
    # english plural folding only; enough to match "tool" and "tools"
    if len(token) > 3 and token.isascii() and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def raw_tokens(text: str) -> list[str]:
    return normalise(expand_hashtags(text)).split()


def tokens(text: str) -> list[str]:
    return [stem(token) for token in raw_tokens(text)]


def content_tokens(text: str) -> list[str]:
    return [stem(token) for token in raw_tokens(URL_LIKE.sub(" ", text)) if token not in STOPWORDS]


def find_phrase(haystack: Sequence[str], phrase: Sequence[str]) -> list[int]:
    if not phrase or len(phrase) > len(haystack):
        return []
    width = len(phrase)
    first = phrase[0]
    return [
        index
        for index in range(len(haystack) - width + 1)
        if haystack[index] == first and list(haystack[index : index + width]) == list(phrase)
    ]


class KeywordPresence:
    """phrase presence that ignores case, diacritics, punctuation, hyphens, plurals and hashtag casing."""

    def __init__(self, text: str) -> None:
        self._tokens = tokens(text)

    def count(self, phrase: str) -> int:
        return len(find_phrase(self._tokens, tokens(phrase)))

    def contains(self, phrase: str) -> bool:
        return self.count(phrase) > 0

    def contains_any(self, phrases: Iterable[str]) -> bool:
        return any(self.contains(phrase) for phrase in phrases)


def ngrams(raw: Sequence[str], max_n: int = 4) -> set[str]:
    # takes unstemmed tokens so stopwords are recognised before plural folding
    stemmed = [stem(token) for token in raw]
    grams: set[str] = set()
    for size in range(1, max_n + 1):
        for index in range(len(raw) - size + 1):
            if size == 1 and raw[index] in STOPWORDS:
                continue
            grams.add(" ".join(stemmed[index : index + size]))
    return grams


def numbers_in(text: str) -> set[str]:
    return {match.rstrip("%").replace(",", "") for match in NUMBER.findall(URL_LIKE.sub(" ", text))}


def jaccard(left: Iterable[str], right: Iterable[str]) -> float:
    left_set, right_set = set(left), set(right)
    if not left_set and not right_set:
        return 1.0
    return len(left_set & right_set) / len(left_set | right_set)


def shingles(text: str, size: int = 6) -> set[tuple[str, ...]]:
    token_list = tokens(text)
    return {tuple(token_list[index : index + size]) for index in range(len(token_list) - size + 1)}


def is_distinctive_alias(alias: str) -> bool:
    # single common words ("fable", "apple") collide with ordinary text, so only full forms count
    alias_tokens = normalise(alias).split()
    return len(alias_tokens) >= 2 or any(char.isdigit() for char in alias)
