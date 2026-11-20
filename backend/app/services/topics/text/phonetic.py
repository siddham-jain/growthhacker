"""latin phonetic skeletons so devanagari, transliterated and hinglish spellings meet on one key."""

import regex

_CONSONANTS = {
    "क": "k",
    "ख": "kh",
    "ग": "g",
    "घ": "gh",
    "ङ": "n",
    "च": "ch",
    "छ": "chh",
    "ज": "j",
    "झ": "jh",
    "ञ": "n",
    "ट": "t",
    "ठ": "th",
    "ड": "d",
    "ढ": "dh",
    "ण": "n",
    "त": "t",
    "थ": "th",
    "द": "d",
    "ध": "dh",
    "न": "n",
    "प": "p",
    "फ": "ph",
    "ब": "b",
    "भ": "bh",
    "म": "m",
    "य": "y",
    "र": "r",
    "ल": "l",
    "ळ": "l",
    "व": "v",
    "श": "sh",
    "ष": "sh",
    "स": "s",
    "ह": "h",
}
_INDEPENDENT_VOWELS = {
    "अ": "a",
    "आ": "aa",
    "इ": "i",
    "ई": "ii",
    "उ": "u",
    "ऊ": "uu",
    "ऋ": "ri",
    "ए": "e",
    "ऐ": "ai",
    "ओ": "o",
    "औ": "au",
    "ऑ": "o",
    "ऍ": "e",
}
_VOWEL_SIGNS = {
    "ा": "aa",
    "ि": "i",
    "ी": "ii",
    "ु": "u",
    "ू": "uu",
    "ृ": "ri",
    "े": "e",
    "ै": "ai",
    "ो": "o",
    "ौ": "au",
    "ॉ": "o",
    "ॅ": "e",
}
_VIRAMA = "्"
_NASALS = {"ं": "n", "ँ": "n", "ः": "h"}
_DIGITS = {chr(0x0966 + digit): str(digit) for digit in range(10)}
_INHERENT = "a"

_LATIN_FOLDS: tuple[tuple[str, str], ...] = (
    ("ksh", "ks"),
    ("shh", "s"),
    ("sh", "s"),
    ("ph", "f"),
    ("aa", "a"),
    ("ee", "i"),
    ("ii", "i"),
    ("oo", "u"),
    ("uu", "u"),
    ("w", "v"),
    ("z", "j"),
    ("q", "k"),
)
_FINAL_EY = regex.compile(r"(?<=\w)ey$")
_DOUBLED = regex.compile(r"(.)\1+")
_NON_LATIN = regex.compile(r"[^a-z0-9]")


class _Unit:
    __slots__ = ("consonant", "nasal", "vowel")

    def __init__(self, consonant: str | None, vowel: str | None) -> None:
        self.consonant = consonant
        self.vowel = vowel
        self.nasal = ""


def _units(word: str) -> list[_Unit]:
    units: list[_Unit] = []
    for char in word:
        if char in _CONSONANTS:
            units.append(_Unit(_CONSONANTS[char], _INHERENT))
        elif char in _INDEPENDENT_VOWELS:
            units.append(_Unit(None, _INDEPENDENT_VOWELS[char]))
        elif char in _VOWEL_SIGNS and units and units[-1].consonant is not None:
            units[-1].vowel = _VOWEL_SIGNS[char]
        elif char == _VIRAMA and units:
            units[-1].vowel = None
        elif char in _NASALS and units:
            units[-1].nasal += _NASALS[char]
        elif char in _DIGITS:
            units.append(_Unit(_DIGITS[char], None))
    return units


def _delete_schwas(units: list[_Unit]) -> None:
    # hindi schwa deletion: word-final, then medial in the v c_a c v context, right to left
    inherent = [unit.consonant is not None and unit.vowel == _INHERENT and not unit.nasal for unit in units]
    if len(units) > 1 and inherent[-1]:
        units[-1].vowel = None
    for index in range(len(units) - 2, 0, -1):
        unit, before, after = units[index], units[index - 1], units[index + 1]
        if not inherent[index] or unit.vowel != _INHERENT:
            continue
        if before.vowel is not None and after.consonant is not None and after.vowel is not None:
            unit.vowel = None


def devanagari_to_latin(word: str) -> str:
    units = _units(word)
    _delete_schwas(units)
    return "".join((unit.consonant or "") + (unit.vowel or "") + unit.nasal for unit in units)


def latin_skeleton(word: str) -> str:
    skeleton = _NON_LATIN.sub("", word.lower())
    for source, target in _LATIN_FOLDS:
        skeleton = skeleton.replace(source, target)
    skeleton = _FINAL_EY.sub("e", skeleton)
    return _DOUBLED.sub(r"\1", skeleton)


def phonetic_key(key: str, script: str | None) -> str | None:
    if script == "Deva":
        latin = devanagari_to_latin(key)
    elif script == "Latn" or key.isdigit():
        latin = key
    else:
        return None
    skeleton = latin_skeleton(latin)
    return skeleton or None
