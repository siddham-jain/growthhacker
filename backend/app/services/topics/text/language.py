from collections.abc import Sequence

from app.services.topics.lexicon import Lexicons
from app.services.topics.text.tokenize import Token

INDIC_SCRIPTS = frozenset({"Deva", "Beng", "Guru", "Gujr", "Orya", "Taml", "Telu", "Knda", "Mlym"})
LEXICON_SCRIPTS = frozenset({"Latn", "Deva"})


def is_hinglish(tokens: Sequence[Token], lexicons: Lexicons) -> bool:
    # latin text carrying hindi function words: >= 2 hits and >= 2 per 12 latin tokens
    latin = [token for token in tokens if token.script == "Latn"]
    if not latin:
        return False
    hits = sum(1 for token in latin if token.stem in lexicons.hinglish_function_words)
    return hits >= 2 and hits * 12 >= 2 * len(latin)


def is_code_mixed(profile: dict[str, float], tokens: Sequence[Token], lexicons: Lexicons) -> bool:
    latin = profile.get("Latn", 0.0)
    indic = sum(share for script, share in profile.items() if script in INDIC_SCRIPTS)
    return (latin >= 0.2 and indic >= 0.2) or is_hinglish(tokens, lexicons)


class NegationDetector:
    def __init__(self, lexicons: Lexicons, window: int) -> None:
        self._cues = lexicons.negation
        self._window = window

    def is_negated(self, tokens: Sequence[Token], start_index: int) -> bool:
        if start_index >= len(tokens):
            return False
        sentence = tokens[start_index].sentence
        for index in range(max(0, start_index - self._window), start_index):
            token = tokens[index]
            if token.sentence == sentence and token.stem in self._cues:
                return True
        return False
