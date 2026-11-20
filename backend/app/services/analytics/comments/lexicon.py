from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

import regex

from app.core.registry import Registry
from app.domain.analytics import IntentClass
from app.services.analytics.traits.lexicons import load_lexicon
from app.services.analytics.traits.title import compile_any, phrase_pattern

LOCALE_KEYS = ("en", "hi_latn", "hi_deva")

# packs are keyed by niche; each pack maps an intent class to phrases per locale
intent_pack_registry: Registry[Mapping[str, Mapping[str, list[str]]]] = Registry("intent_pack_registry")


def _register_yaml_packs() -> str:
    data = load_lexicon("intents")
    intent_pack_registry.add("base", data["base"])
    for niche, pack in sorted(data["niches"].items()):
        intent_pack_registry.add(niche, pack)
    return data["version"]


LEXICON_VERSION = _register_yaml_packs()


@dataclass(frozen=True, slots=True)
class IntentLexicon:
    version: str
    phrases: Mapping[IntentClass, regex.Pattern[str] | None]
    patterns: Mapping[IntentClass, regex.Pattern[str] | None]
    exclusions: Mapping[IntentClass, regex.Pattern[str] | None]
    spam: regex.Pattern[str] | None

    def classes(self, text: str, location_terms: Iterable[str] = ()) -> frozenset[IntentClass]:
        found = set()
        locations = phrase_pattern(location_terms)
        for intent in IntentClass:
            cleaned = text
            exclusion = self.exclusions.get(intent)
            if exclusion is not None:
                cleaned = exclusion.sub(" ", cleaned)
            phrase, pattern = self.phrases.get(intent), self.patterns.get(intent)
            if (phrase and phrase.search(cleaned)) or (pattern and pattern.search(cleaned)):
                found.add(intent)
        if locations is not None and locations.search(text):
            found.add(IntentClass.location)
        return frozenset(found)

    def is_spam(self, text: str) -> bool:
        return bool(self.spam and self.spam.search(text))


def _merge(packs: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, list[str]]]:
    merged: dict[str, dict[str, list[str]]] = {}
    for pack in packs:
        for intent, locales in pack.items():
            target = merged.setdefault(intent, {})
            for locale, items in locales.items():
                target.setdefault(locale, []).extend(items)
    return merged


def build_lexicon(niche: str | None) -> IntentLexicon:
    data = load_lexicon("intents")
    packs = [intent_pack_registry.get("base")]
    if niche and niche in intent_pack_registry:
        packs.append(intent_pack_registry.get(niche))
    merged = _merge(packs)
    phrases, patterns = {}, {}
    for intent in IntentClass:
        locales = merged.get(intent.value, {})
        phrases[intent] = phrase_pattern(term for key in LOCALE_KEYS for term in locales.get(key, []))
        # symbols such as the rupee sign attach to digits, so they match without word boundaries
        raw = locales.get("patterns", []) + [regex.escape(symbol) for symbol in locales.get("symbols", [])]
        patterns[intent] = compile_any(raw, 0) if raw else None
    exclusions = {IntentClass(k): phrase_pattern(v) for k, v in data.get("exclusions", {}).items()}
    spam_terms = [term for terms in data.get("spam", {}).values() for term in terms]
    return IntentLexicon(
        version=f"{data['version']}+{niche or 'base'}",
        phrases=phrases,
        patterns=patterns,
        exclusions=exclusions,
        spam=phrase_pattern(spam_terms),
    )
