from bisect import bisect_right
from collections.abc import Collection
from dataclasses import dataclass

import regex

from app.services.topics.text.morphology import stem_key
from app.services.topics.text.normalize import fold
from app.services.topics.text.phonetic import phonetic_key
from app.services.topics.text.scripts import script_of

WORD = regex.compile(r"[\p{L}\p{M}\p{N}]+(?:'[\p{L}]+)*")
SENTENCE_BREAK = regex.compile(r"[.!?।|\n•;:]+|\s[-–—]\s")
EMOJI = regex.compile(r"\p{Extended_Pictographic}")
CAMEL = regex.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+|[^\W\d_]+")


@dataclass(frozen=True, slots=True)
class Token:
    text: str
    start: int
    end: int
    key: str
    stem: str
    phonetic: str | None
    script: str | None
    sentence: int
    is_number: bool
    is_upper: bool


def _token_script(text: str) -> str | None:
    for char in text:
        script = script_of(char)
        if script is not None:
            return script
    return None


def tokenize(text: str) -> list[Token]:
    breaks = [match.start() for match in SENTENCE_BREAK.finditer(text)]
    tokens: list[Token] = []
    for match in WORD.finditer(text):
        raw = match.group(0)
        key = fold(raw)
        script = _token_script(raw)
        is_number = key.isdigit()
        is_upper = raw.isupper() and 2 <= len(raw) <= 6 and script == "Latn"
        tokens.append(
            Token(
                text=raw,
                start=match.start(),
                end=match.end(),
                key=key,
                stem=stem_key(key, script, acronym=is_upper),
                phonetic=phonetic_key(key, script),
                script=script,
                sentence=bisect_right(breaks, match.start()),
                is_number=is_number,
                is_upper=is_upper,
            )
        )
    return tokens


def stems(text: str) -> tuple[str, ...]:
    return tuple(token.stem for token in tokenize(text))


def count_emoji(text: str) -> int:
    return len(EMOJI.findall(text))


def split_hashtag(hashtag: str, vocabulary: Collection[str]) -> str:
    """#KneeReplacement -> knee replacement; #kneereplacementsurgery via max-match over the vocabulary."""
    body = hashtag.lstrip("#")
    if "_" in body:
        return " ".join(part for part in body.split("_") if part)
    has_upper = any(char.isupper() for char in body)
    has_lower = any(char.islower() for char in body)
    if has_upper and has_lower:
        return " ".join(CAMEL.findall(body))
    if not body.isascii():
        return body
    return _segment(body.lower(), vocabulary) or body


def _segment(text: str, vocabulary: Collection[str]) -> str | None:
    # fewest-words segmentation where every piece is a known word or a number
    best: list[list[str] | None] = [None] * (len(text) + 1)
    best[0] = []
    for end in range(1, len(text) + 1):
        for start in range(max(0, end - 24), end):
            prefix = best[start]
            piece = text[start:end]
            if prefix is None or not (piece in vocabulary or piece.isdigit()):
                continue
            candidate = [*prefix, piece]
            current = best[end]
            if current is None or len(candidate) < len(current):
                best[end] = candidate
    segmented = best[len(text)]
    return " ".join(segmented) if segmented and len(segmented) > 1 else None
