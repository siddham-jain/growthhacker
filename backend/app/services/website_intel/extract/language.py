import re
import unicodedata

import py3langid

MIN_STATISTICAL_CHARS = 200
SCRIPT_SHARE = 0.3
HINGLISH_SHARE = 0.15
SCRIPT_LANGUAGES = (
    ("DEVANAGARI", "hi", "Deva"),
    ("TAMIL", "ta", "Taml"),
    ("BENGALI", "bn", "Beng"),
    ("TELUGU", "te", "Telu"),
    ("KANNADA", "kn", "Knda"),
    ("MALAYALAM", "ml", "Mlym"),
    ("GUJARATI", "gu", "Gujr"),
    ("GURMUKHI", "pa", "Guru"),
    ("ORIYA", "or", "Orya"),
    ("ARABIC", "ar", "Arab"),
    ("HANGUL", "ko", "Kore"),
    ("HIRAGANA", "ja", "Jpan"),
    ("KATAKANA", "ja", "Jpan"),
    ("CJK", "zh", "Hani"),
    ("THAI", "th", "Thai"),
)
# romanised hindi function words and common verbs; data, not logic
HINGLISH_LEXICON = frozenset(
    [
        "hai",
        "hain",
        "ho",
        "hota",
        "hoti",
        "hote",
        "tha",
        "thi",
        "kya",
        "kaise",
        "kyun",
        "kyon",
        "kab",
        "kahan",
        "kaun",
        "kitna",
        "kitne",
        "kitni",
        "aap",
        "aapka",
        "aapki",
        "aapke",
        "hum",
        "humara",
        "hamara",
        "mera",
        "meri",
        "mere",
        "tum",
        "tumhara",
        "yeh",
        "ye",
        "woh",
        "wo",
        "isme",
        "usme",
        "iska",
        "uska",
        "ki",
        "ka",
        "ke",
        "ko",
        "se",
        "mein",
        "par",
        "aur",
        "ya",
        "lekin",
        "nahi",
        "nahin",
        "bhi",
        "sirf",
        "bahut",
        "accha",
        "acha",
        "theek",
        "sahi",
        "abhi",
        "jaldi",
        "karein",
        "kare",
        "karo",
        "karna",
        "karte",
        "kijiye",
        "dekhiye",
        "jaaniye",
        "janiye",
        "liye",
        "wala",
        "wali",
        "wale",
        "chahiye",
        "sakte",
        "sakta",
        "sakti",
        "rahe",
        "raha",
        "rahi",
        "gaya",
        "gayi",
        "hua",
        "hui",
        "ilaaj",
        "ilaj",
        "dawai",
        "sahab",
        "bimari",
        "dard",
    ]
)
_LATIN_WORD = re.compile(r"[a-z]+")


def _script_of(char: str) -> str | None:
    name = unicodedata.name(char, "")
    for prefix, _, _ in SCRIPT_LANGUAGES:
        if name.startswith(prefix):
            return prefix
    return None


def dominant_script(text: str) -> tuple[str, str] | None:
    """(language, iso 15924 script) when a non-latin script makes up most letters."""
    counts: dict[str, int] = {}
    letters = 0
    for char in text:
        if not char.isalpha():
            continue
        letters += 1
        script = _script_of(char)
        if script is not None:
            counts[script] = counts.get(script, 0) + 1
    if not letters or not counts:
        return None
    script, count = max(counts.items(), key=lambda item: item[1])
    if count / letters < SCRIPT_SHARE:
        return None
    language, code = next((lang, code) for prefix, lang, code in SCRIPT_LANGUAGES if prefix == script)
    return language, code


def hinglish_share(text: str) -> float:
    words = _LATIN_WORD.findall(text.casefold())
    if not words:
        return 0.0
    return sum(word in HINGLISH_LEXICON for word in words) / len(words)


def normalize_tag(tag: str) -> str:
    parts = tag.replace("_", "-").strip().split("-")
    head = parts[0].lower()
    tail = [part.title() if len(part) == 4 else part.upper() for part in parts[1:] if part]
    return "-".join([head, *tail])


class LanguageDetector:
    """declared lang, then statistical id, then script, then a hinglish lexicon (plan 03 §5.7.8)."""

    def detect(self, text: str, declared: str | None = None) -> str | None:
        script = dominant_script(text)
        romanised_hindi = script is None and hinglish_share(text) >= HINGLISH_SHARE
        if declared:
            tag = normalize_tag(declared)
            return "hi-Latn" if romanised_hindi and tag.split("-")[0] in {"en", "hi"} else tag
        if romanised_hindi:
            return "hi-Latn"
        if len(text) >= MIN_STATISTICAL_CHARS:
            language, _ = py3langid.classify(text)
            return language
        return script[0] if script else None
