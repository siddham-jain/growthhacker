import unicodedata

from app.domain.card_copy import Violation
from app.services.llm.writer.evidence_pack import BuiltPack
from app.services.llm.writer.schemas import CardCopyLLM
from app.services.llm.writer.validators.base import copy_fields, grounding_validators, masked_text

LATIN_LANGUAGES = frozenset({"en", "es", "fr", "de", "pt", "it", "nl", "id", "ms", "tl", "sw", "tr", "vi"})
SCRIPT_BY_LANGUAGE = {
    "hi": "DEVANAGARI",
    "mr": "DEVANAGARI",
    "ne": "DEVANAGARI",
    "ta": "TAMIL",
    "te": "TELUGU",
    "kn": "KANNADA",
    "ml": "MALAYALAM",
    "bn": "BENGALI",
    "gu": "GUJARATI",
    "pa": "GURMUKHI",
    "ar": "ARABIC",
    "ur": "ARABIC",
    "ru": "CYRILLIC",
    "uk": "CYRILLIC",
}
MAX_FOREIGN_SHARE_LATIN = 0.30
MIN_SCRIPT_SHARE = 0.40
MIN_LETTERS = 8


def script_of(char: str) -> str:
    name = unicodedata.name(char, "")
    return name.split(" ", 1)[0] if name else ""


class LanguageCheck:
    """V8: output script must fit the requested language; same-script mix-ups are left to the critic."""

    name = "language"

    def validate(self, copy: CardCopyLLM, built: BuiltPack) -> list[Violation]:
        language = built.grounding.language.split("-")[0].lower()
        target = "LATIN" if language in LATIN_LANGUAGES else SCRIPT_BY_LANGUAGE.get(language)
        if target is None:
            return []
        violations = []
        for field in copy_fields(copy):
            letters = [char for char in masked_text(field.text, built) if char.isalpha()]
            if len(letters) < MIN_LETTERS:
                continue
            share = sum(1 for char in letters if script_of(char) == target) / len(letters)
            wrong = (1 - share) > MAX_FOREIGN_SHARE_LATIN if target == "LATIN" else share < MIN_SCRIPT_SHARE
            if wrong:
                violations.append(
                    Violation(
                        rule="wrong_language", field=field.name, detail=f"write this field in '{language}'"
                    )
                )
        return violations


grounding_validators.add("language", lambda settings: LanguageCheck())
