import unicodedata

import regex

ZERO_WIDTH = regex.compile(r"[​-‏⁠-⁤﻿︀-️\U000e0100-\U000e01ef]")
QUOTES = str.maketrans({"‘": "'", "’": "'", "‚": "'", "“": '"', "”": '"', "′": "'"})
INLINE_SPACE = regex.compile(r"[^\S\n]+")
ANY_SPACE = regex.compile(r"\s+")
NUKTA = "़"
CHANDRABINDU, ANUSVARA = "ँ", "ं"
LATIN_LETTER = regex.compile(r"\p{Latin}")
WORD = regex.compile(r"\S+")
# latin lookalikes used to dodge keyword filters; only folded inside words that also contain latin
HOMOGLYPHS = str.maketrans(
    {
        "а": "a",
        "е": "e",
        "о": "o",
        "р": "p",
        "с": "c",
        "у": "y",
        "х": "x",
        "і": "i",
        "ј": "j",
        "ѕ": "s",
        "ԁ": "d",
        "ӏ": "l",
        "ο": "o",
        "α": "a",
        "ν": "v",
        "τ": "t",
        "κ": "k",
        "ɡ": "g",
    }
)


def clean_text(text: str) -> str:
    """nfkc, zero-width and variation selectors stripped, straight quotes, collapsed inline spaces."""
    text = unicodedata.normalize("NFKC", text or "")
    text = ZERO_WIDTH.sub("", text).translate(QUOTES)
    lines = [INLINE_SPACE.sub(" ", line).strip() for line in text.replace("\r\n", "\n").split("\n")]
    return "\n".join(lines).strip()


def _strip_latin_diacritics(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text)
    out: list[str] = []
    for char in decomposed:
        if unicodedata.combining(char) and out and LATIN_LETTER.match(out[-1]):
            continue
        out.append(char)
    return unicodedata.normalize("NFC", "".join(out))


def _fold_homoglyphs(text: str) -> str:
    def fold_word(match: regex.Match[str]) -> str:
        word = match.group(0)
        return word.translate(HOMOGLYPHS) if LATIN_LETTER.search(word) else word

    return WORD.sub(fold_word, text)


def fold(text: str) -> str:
    """matching key form: casefold, nukta and chandrabindu folding, latin diacritics, homoglyphs."""
    text = _fold_homoglyphs(text).casefold()
    text = unicodedata.normalize("NFD", text).replace(NUKTA, "").replace(CHANDRABINDU, ANUSVARA)
    return _strip_latin_diacritics(unicodedata.normalize("NFC", text))


def normalize(text: str) -> str:
    return ANY_SPACE.sub(" ", fold(clean_text(text))).strip()
