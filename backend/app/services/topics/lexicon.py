import hashlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import regex
import yaml

from app.domain.profile import FunnelStage
from app.domain.topics import Facet
from app.services.topics.text.normalize import fold
from app.services.topics.text.tokenize import stems

LEXICON_DIR = Path(__file__).parent / "lexicons"


class PhraseSet:
    """phrases as stem-key tuples; finds every occurrence inside a key sequence."""

    def __init__(self, phrases: Iterable[str]) -> None:
        self._by_first: dict[str, list[tuple[str, ...]]] = {}
        for phrase in phrases:
            keys = stems(phrase)
            if keys:
                self._by_first.setdefault(keys[0], []).append(keys)
        for entries in self._by_first.values():
            entries.sort(key=len, reverse=True)

    def find(self, keys: Sequence[str]) -> list[tuple[int, int]]:
        found = []
        for index, key in enumerate(keys):
            for phrase in self._by_first.get(key, ()):
                if tuple(keys[index : index + len(phrase)]) == phrase:
                    found.append((index, len(phrase)))
                    break
        return found

    def contains(self, keys: Sequence[str]) -> bool:
        return bool(self.find(keys))

    def __contains__(self, key: object) -> bool:
        return any(len(phrase) == 1 for phrase in self._by_first.get(str(key), ()))


@dataclass(frozen=True)
class Lexicons:
    stopwords: frozenset[str]
    question_words: frozenset[str]
    hinglish_function_words: frozenset[str]
    negation: frozenset[str]
    generic: frozenset[str]
    common_words: frozenset[str]
    ambiguous_acronyms: frozenset[str]
    healthcare_exempt_acronyms: frozenset[str]
    hashtag_words: frozenset[str]
    facets: Mapping[Facet, PhraseSet]
    facet_stage: tuple[tuple[FunnelStage, frozenset[Facet]], ...]
    intent: PhraseSet
    promo: PhraseSet
    cta: PhraseSet
    link_list_prefixes: tuple[str, ...]
    specificity: Mapping[Facet, tuple[regex.Pattern[str], ...]]
    content_hash: str


def _flatten(section: Any) -> list[str]:
    if isinstance(section, Mapping):
        return [str(item) for values in section.values() for item in values]
    return [str(item) for item in section or []]


def _stem_set(words: Iterable[str]) -> frozenset[str]:
    return frozenset(key for word in words for key in stems(word))


@lru_cache(maxsize=1)
def load_lexicons() -> Lexicons:
    raw_texts = {path.name: path.read_text("utf-8") for path in sorted(LEXICON_DIR.glob("*.yaml"))}
    digest = hashlib.sha256("".join(f"{name}\n{text}" for name, text in raw_texts.items()).encode())
    core = yaml.safe_load(raw_texts["core.yaml"])
    cues = yaml.safe_load(raw_texts["cues.yaml"])
    return Lexicons(
        stopwords=_stem_set(_flatten(core["stopwords"])),
        question_words=_stem_set(_flatten(core["question_words"])),
        hinglish_function_words=_stem_set(core["hinglish_function_words"]),
        negation=_stem_set(_flatten(core["negation"])),
        generic=_stem_set(_flatten(core["generic"])),
        common_words=_stem_set(core["common_words"]),
        ambiguous_acronyms=frozenset(core["ambiguous_acronyms"]),
        healthcare_exempt_acronyms=frozenset(core["healthcare_exempt_acronyms"]),
        hashtag_words=frozenset(fold(word) for word in core["hashtag_words"]),
        facets={Facet(name): PhraseSet(_flatten(section)) for name, section in cues["facets"].items()},
        facet_stage=tuple(
            (FunnelStage(stage), frozenset(Facet(name) for name in names))
            for stage, names in cues["facet_stage"].items()
        ),
        intent=PhraseSet(_flatten(cues["intent"])),
        promo=PhraseSet(_flatten(cues["promo"])),
        cta=PhraseSet(_flatten(cues["cta"])),
        link_list_prefixes=tuple(fold(prefix) for prefix in cues["link_list_prefixes"]),
        specificity={
            Facet(name): tuple(regex.compile(pattern) for pattern in patterns)
            for name, patterns in cues["specificity"].items()
        },
        content_hash=digest.hexdigest()[:12],
    )
