"""light, deterministic stem keys; keys are only used for matching and never shown to anyone."""

_ENGLISH_SUFFIXES: tuple[tuple[str, str, int], ...] = (
    # (suffix, replacement, minimum stem length left behind)
    ("ies", "y", 3),
    ("sses", "ss", 2),
    ("ments", "", 4),
    ("ment", "", 4),
    ("ations", "", 3),
    ("ation", "", 3),
    ("ings", "", 3),
    ("ing", "", 3),
    ("ied", "y", 3),
    ("ed", "", 3),
    ("s", "", 3),
)
_KEEP_FINAL_S = ("ss", "us", "is")

# longest first so oblique plurals fold before singular endings
_HINDI_SUFFIXES: tuple[str, ...] = (
    "ियों",
    "ियाँ",
    "ियां",
    "ाओं",
    "ाएं",
    "ाएँ",
    "ों",
    "ें",
    "ीं",
    "ां",
    "ा",
    "े",
    "ी",
)


def english_stem(word: str) -> str:
    if len(word) <= 3 or not word.isascii() or not word.isalpha():
        return word
    stem = word
    for suffix, replacement, min_stem in _ENGLISH_SUFFIXES:
        if not stem.endswith(suffix):
            continue
        if suffix == "s" and stem.endswith(_KEEP_FINAL_S):
            break
        base = stem[: -len(suffix)]
        if len(base) >= min_stem:
            stem = base + replacement
        break
    if len(stem) > 3 and stem.endswith("e"):
        stem = stem[:-1]
    return stem


def hindi_stem(word: str) -> str:
    for suffix in _HINDI_SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 2:
            return word[: -len(suffix)]
    return word


def stem_key(key: str, script: str | None, *, acronym: bool = False) -> str:
    if acronym or key.isdigit():
        return key
    if script == "Latn":
        return english_stem(key)
    if script == "Deva":
        return hindi_stem(key)
    return key
