import unicodedata
from bisect import bisect_right

# (first codepoint, last codepoint, iso 15924 code); anything else with a letter is "Zyyy"
_RANGES: tuple[tuple[int, int, str], ...] = (
    (0x0041, 0x024F, "Latn"),
    (0x0370, 0x03FF, "Grek"),
    (0x0400, 0x052F, "Cyrl"),
    (0x0600, 0x06FF, "Arab"),
    (0x0750, 0x077F, "Arab"),
    (0x0900, 0x097F, "Deva"),
    (0x0980, 0x09FF, "Beng"),
    (0x0A00, 0x0A7F, "Guru"),
    (0x0A80, 0x0AFF, "Gujr"),
    (0x0B00, 0x0B7F, "Orya"),
    (0x0B80, 0x0BFF, "Taml"),
    (0x0C00, 0x0C7F, "Telu"),
    (0x0C80, 0x0CFF, "Knda"),
    (0x0D00, 0x0D7F, "Mlym"),
    (0x1E00, 0x1EFF, "Latn"),
    (0xA8E0, 0xA8FF, "Deva"),
)
_STARTS = [start for start, _, _ in _RANGES]


def script_of(char: str) -> str | None:
    """script of a letter or combining mark; none for digits, punctuation and symbols."""
    category = unicodedata.category(char)
    if not (category.startswith("L") or category.startswith("M")):
        return None
    codepoint = ord(char)
    index = bisect_right(_STARTS, codepoint) - 1
    if index >= 0:
        start, end, script = _RANGES[index]
        if start <= codepoint <= end:
            return script
    return "Zyyy"


def script_profile(text: str) -> dict[str, float]:
    counts: dict[str, int] = {}
    for char in text:
        script = script_of(char)
        if script is not None:
            counts[script] = counts.get(script, 0) + 1
    total = sum(counts.values())
    if not total:
        return {}
    return {script: round(count / total, 4) for script, count in sorted(counts.items())}


def dominant_script(profile: dict[str, float]) -> str | None:
    if not profile:
        return None
    return max(sorted(profile), key=lambda script: profile[script])
