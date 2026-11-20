import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LexiconHit:
    pattern_id: str
    text: str


class Lexicon:
    """configurable regex list; every hit carries its pattern id so false positives can be tuned."""

    def __init__(self, patterns: Mapping[str, str]) -> None:
        self._patterns = {
            key: re.compile(pattern, re.IGNORECASE) for key, pattern in sorted(patterns.items())
        }

    def find(self, texts: Iterable[str]) -> list[LexiconHit]:
        hits = []
        for text in texts:
            for pattern_id, pattern in self._patterns.items():
                match = pattern.search(text)
                if match:
                    hits.append(LexiconHit(pattern_id=pattern_id, text=match.group(0)))
        return hits
