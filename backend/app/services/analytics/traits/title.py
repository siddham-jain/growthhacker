import unicodedata
from collections.abc import Iterable
from functools import cached_property, lru_cache

import regex

from app.core.registry import trait_extractor_registry
from app.domain.analytics import TitleTraits
from app.domain.youtube import VideoRecord
from app.services.analytics.traits.base import Features, TraitContext, flag
from app.services.analytics.traits.lexicons import load_lexicon

YEAR = regex.compile(r"(?<!\d)20[0-3]\d(?!\d)")
DIGITS = regex.compile(r"\d+")
EMOJI = regex.compile(r"\p{Extended_Pictographic}")
WORDS = regex.compile(r"[\p{L}\p{M}']+")
TOKEN_EDGE = regex.compile(r"^[^\p{L}\p{N}]+|[^\p{L}\p{N}]+$")
DEVANAGARI = regex.compile(r"[ऀ-ॿ]")
LATIN = regex.compile(r"\p{Latin}")
LETTER = regex.compile(r"[\p{L}\p{M}]")
WHITESPACE = regex.compile(r"\s+")


def normalise_text(text: str) -> str:
    return WHITESPACE.sub(" ", unicodedata.normalize("NFKC", text)).strip()


def compile_any(patterns: list[str], flags: int = regex.IGNORECASE) -> regex.Pattern[str]:
    return regex.compile("|".join(f"(?:{p})" for p in patterns), flags)


def phrase_pattern(terms: Iterable[str]) -> regex.Pattern[str] | None:
    cleaned = tuple(sorted({t.strip() for t in terms if t.strip()}, key=lambda t: (-len(t), t)))
    return _phrase_pattern(cleaned) if cleaned else None


@lru_cache(maxsize=256)
def _phrase_pattern(cleaned: tuple[str, ...]) -> regex.Pattern[str]:
    alternatives = "|".join(regex.escape(term) for term in cleaned)
    return regex.compile(
        rf"(?<![\p{{L}}\p{{M}}\p{{N}}])(?:{alternatives})(?![\p{{L}}\p{{M}}\p{{N}}])", regex.IGNORECASE
    )


def band(value: float, edges: list[tuple[float, str]], last: str) -> str:
    for upper, label in edges:
        if value <= upper:
            return label
    return last


def caps_band(ratio: float | None) -> str | None:
    if ratio is None:
        return None
    if ratio < 0.3:
        return "<0.3"
    return "0.3-0.6" if ratio <= 0.6 else ">0.6"


def script_of(text: str) -> str:
    letters = LETTER.findall(text)
    if not letters:
        return "latin"
    deva = sum(1 for ch in letters if DEVANAGARI.match(ch)) / len(letters)
    if deva >= 0.8:
        return "devanagari"
    if deva >= 0.2:
        return "mixed"
    latin = sum(1 for ch in letters if LATIN.match(ch)) / len(letters)
    return "latin" if latin >= 0.5 else "other"


def separator_of(text: str) -> str:
    kinds = []
    if ":" in text:
        kinds.append("colon")
    if "|" in text:
        kinds.append("pipe")
    if regex.search(r"\s[-–—]\s", text):
        kinds.append("dash")
    if not kinds:
        return "none"
    return kinds[0] if len(kinds) == 1 else "multiple"


class TitleLexicon:
    def __init__(self) -> None:
        self.data = load_lexicon("title_patterns")
        self.version = self.data["version"]

    @cached_property
    def interrogative_starters(self) -> frozenset[str]:
        groups = self.data["interrogatives"]
        return frozenset(word.casefold() for words in groups.values() for word in words)

    @cached_property
    def hindi_interrogatives(self) -> regex.Pattern[str]:
        groups = self.data["interrogatives"]
        return phrase_pattern(groups["hi_latn"] + groups["hi_deva"])

    def pattern(self, name: str, flags: int = regex.IGNORECASE) -> regex.Pattern[str]:
        return compile_any(self.data[name], flags)

    @cached_property
    def how_to(self) -> regex.Pattern[str]:
        return self.pattern("how_to")

    @cached_property
    def listicle(self) -> regex.Pattern[str]:
        return self.pattern("listicle")

    @cached_property
    def first_person(self) -> regex.Pattern[str]:
        return self.pattern("first_person")

    @cached_property
    def superlatives(self) -> regex.Pattern[str]:
        return self.pattern("superlatives")

    @cached_property
    def price(self) -> regex.Pattern[str]:
        return self.pattern("price_cost")

    @cached_property
    def price_exclusions(self) -> regex.Pattern[str]:
        return phrase_pattern(self.data["price_exclusions"])

    @cached_property
    def buyer_phrasing(self) -> regex.Pattern[str]:
        return self.pattern("buyer_question_phrasing")

    @cached_property
    def doctor(self) -> regex.Pattern[str]:
        return compile_any(self.data["doctor"], 0)

    @cached_property
    def near_me(self) -> regex.Pattern[str]:
        return regex.compile(self.data["near_me"], regex.IGNORECASE)

    @cached_property
    def acronyms(self) -> frozenset[str]:
        return frozenset(self.data["acronyms"])


def is_question(text: str, lexicon: TitleLexicon) -> bool:
    if "?" in text or "？" in text:
        return True
    tokens = text.split()
    first = TOKEN_EDGE.sub("", tokens[0]).casefold() if tokens else ""
    return first in lexicon.interrogative_starters or bool(lexicon.hindi_interrogatives.search(text))


@trait_extractor_registry.register("title")
class TitleTraitExtractor:
    key = "title"
    scope = "intrinsic"

    def __init__(self) -> None:
        self.lexicon = TitleLexicon()
        self.version = self.lexicon.version

    def extract(self, video: VideoRecord, ctx: TraitContext) -> TitleTraits:
        text = normalise_text(video.title)
        lex = self.lexicon
        years = [int(m.group()) for m in YEAR.finditer(text)]
        numbers = [m for m in DIGITS.finditer(text) if not YEAR.fullmatch(m.group())]
        cased = [ch for ch in text if ch.upper() != ch.lower()]
        caps_ratio = sum(ch.isupper() for ch in cased) / len(cased) if cased else None
        priced = lex.price_exclusions.sub(" ", text)
        emoji_count = len(EMOJI.findall(text))
        location = self._location_pattern(ctx)
        client_location = bool(location and location.search(text))
        return TitleTraits(
            char_len=len(text),
            char_len_band=band(len(text), [(30, "0-30"), (50, "31-50"), (70, "51-70")], ">70"),
            word_count=len(text.split()),
            is_question=is_question(text, lex),
            has_number=bool(numbers),
            has_listicle_number=bool(lex.listicle.search(text)),
            is_how_to=bool(lex.how_to.search(text)),
            has_year=bool(years),
            year_is_current_or_next=any(y in (ctx.as_of.year, ctx.as_of.year + 1) for y in years),
            has_brackets=bool(regex.search(r"\[[^\]]*\]", text)),
            has_parentheses=bool(regex.search(r"\([^)]*\)", text)),
            is_first_person=bool(lex.first_person.search(text)),
            has_superlative=bool(lex.superlatives.search(text)),
            caps_ratio=caps_ratio,
            caps_band=caps_band(caps_ratio),
            all_caps_word_count=self._all_caps_words(text),
            has_emoji=emoji_count > 0,
            emoji_count=emoji_count,
            separator=separator_of(text),
            has_price_cost=bool(lex.price.search(priced)),
            has_buyer_question_phrasing=bool(lex.buyer_phrasing.search(text)),
            mentions_doctor=bool(lex.doctor.search(text)),
            script=script_of(text),
            has_client_location=client_location,
            has_niche_location=client_location or bool(lex.near_me.search(text)),
        )

    def _all_caps_words(self, text: str) -> int:
        count = 0
        for word in WORDS.findall(text):
            cased = [ch for ch in word if ch.upper() != ch.lower()]
            if len(cased) >= 3 and all(ch.isupper() for ch in cased) and word not in self.lexicon.acronyms:
                count += 1
        return count

    @staticmethod
    def _location_pattern(ctx: TraitContext) -> regex.Pattern[str] | None:
        return phrase_pattern(ctx.client.location_terms)

    def mined(self, part: TitleTraits) -> Features:
        booleans = (
            "is_question",
            "has_number",
            "has_listicle_number",
            "is_how_to",
            "has_year",
            "year_is_current_or_next",
            "has_brackets",
            "has_parentheses",
            "is_first_person",
            "has_superlative",
            "has_emoji",
            "has_price_cost",
            "has_buyer_question_phrasing",
            "mentions_doctor",
            "has_client_location",
            "has_niche_location",
        )
        features: Features = {f"title.{name}": flag(getattr(part, name)) for name in booleans}
        features["title.char_len_band"] = frozenset({part.char_len_band})
        features["title.separator"] = frozenset({part.separator})
        features["title.script"] = frozenset({part.script})
        features["title.has_all_caps_word"] = flag(part.all_caps_word_count > 0)
        if part.caps_band is not None:
            features["title.caps_band"] = frozenset({part.caps_band})
        return features
