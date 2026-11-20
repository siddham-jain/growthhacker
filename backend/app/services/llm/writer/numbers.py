import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Literal

TokenKind = Literal[
    "ratio",
    "percent",
    "magnitude",
    "duration",
    "year",
    "date",
    "plain",
    "multiple_word",
    "count_word",
    "fraction",
]
MASK = " ⁣ "
MULTIPLIERS = {
    "k": 1e3,
    "thousand": 1e3,
    "m": 1e6,
    "mn": 1e6,
    "million": 1e6,
    "b": 1e9,
    "bn": 1e9,
    "billion": 1e9,
    "lakh": 1e5,
    "lakhs": 1e5,
    "lac": 1e5,
    "lacs": 1e5,
    "crore": 1e7,
    "crores": 1e7,
    "cr": 1e7,
}
DURATION_SECONDS = {"h": 3600, "m": 60, "s": 1}
MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}  # fmt: skip
COUNT_WORDS = {
    "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
    "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100, "dozen": 12,
}  # fmt: skip
MULTIPLE_WORDS = {"twice": 2.0, "double": 2.0, "doubled": 2.0, "triple": 3.0, "tripled": 3.0, "thrice": 3.0}
FRACTION_WORDS = {"half": 50.0, "a third": 100 / 3, "a quarter": 25.0}
FRACTION_TOLERANCE = 2.0
UNITS_FOR_KIND: dict[str, frozenset[str] | None] = {
    "ratio": frozenset({"ratio_x"}),
    "multiple_word": frozenset({"ratio_x"}),
    "percent": frozenset({"percent"}),
    "fraction": frozenset({"percent"}),
    "magnitude": frozenset({"count", "views", "videos", "channels", "currency"}),
    "duration": frozenset({"minutes", "seconds"}),
    "plain": None,
    "count_word": None,
}

NUMBER = r"\d+(?:\.\d+)?"
MONTH = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
WORDS = "|".join(sorted([*COUNT_WORDS, *MULTIPLE_WORDS, *FRACTION_WORDS], key=len, reverse=True))
PATTERN = re.compile(
    rf"""
    (?P<range>(?<![\w.])(?P<r1>{NUMBER})\s*x?\s*(?:-|to)\s*(?P<r2>{NUMBER})\s*x\b)
    |(?P<iso>\b(?P<iy>\d{{4}})-(?P<im>\d{{2}})-(?P<id>\d{{2}})\b)
    |(?P<dmy>\b(?P<dd>\d{{1,2}})\s+(?P<dmon>{MONTH})\b\.?(?:,?\s+(?P<dyear>\d{{4}})\b)?)
    |(?P<mdy>\b(?P<mmon>{MONTH})\.?\s+(?P<md>\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s+(?P<myear>\d{{4}})\b)?)
    |(?P<ratio>(?<![\w.])(?P<rv>{NUMBER})\s*(?:x|times)\b)
    |(?P<pct>(?<![\w.])(?P<pv>{NUMBER})\s*(?:%|percent\b|per\s+cent\b))
    |(?P<abbr>(?<![\w.])(?P<av>{NUMBER})\s*(?P<au>thousand|million|billion|lakhs?|lacs?|crores?|mn|bn|cr|k|m|b)\b)
    |(?P<indian>(?<![\w.,])\d{{1,2}}(?:,\d{{2}})+,\d{{3}}(?:\.\d+)?(?![\d,]))
    |(?P<western>(?<![\w.,])\d{{1,3}}(?:,\d{{3}})+(?:\.\d+)?(?![\d,]))
    |(?P<dur>(?<![\w.])(?P<duv>{NUMBER})\s*-?\s*(?P<duu>hours?|hrs?|minutes?|mins?|seconds?|secs?)\b)
    |(?P<plain>(?<![\w.])\d+(?:\.\d+)?(?!\w))
    |(?P<word>\b(?:{WORDS})\b)
    """,
    re.IGNORECASE | re.VERBOSE,
)


@dataclass(frozen=True, slots=True)
class AllowedNumber:
    value: float
    unit: str
    display: str


@dataclass(frozen=True, slots=True)
class NumberToken:
    text: str
    value: float
    kind: TokenKind
    tolerance: float
    month_day: tuple[int, int] | None = None
    year: int | None = None


def normalize_numbers(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    out = []
    for char in normalized:
        if char.isdigit() and not char.isascii():
            digit = unicodedata.decimal(char, None)
            out.append(str(digit) if digit is not None else char)
        elif char in "×✕✖":
            out.append("x")
        elif char in "‐‑‒–—−":
            out.append("-")
        elif char in "   ":
            out.append(" ")
        else:
            out.append(char)
    return "".join(out)


def mask_spans(text: str, spans: Iterable[str]) -> str:
    # allowed names and quoted strings may contain digits ("5-Minute Crafts", "24/7 Pharmacy")
    for span in sorted({normalize_numbers(s) for s in spans if s.strip()}, key=len, reverse=True):
        text = re.sub(re.escape(span), MASK, text, flags=re.IGNORECASE)
    return text


def _decimals(raw: str) -> int:
    return len(raw.split(".", 1)[1]) if "." in raw else 0


def _half_unit(raw: str, scale: float = 1.0) -> float:
    return 0.5 * 10 ** (-_decimals(raw)) * scale


def _plain_token(raw: str) -> NumberToken:
    value = float(raw)
    if "." not in raw and len(raw) == 4 and raw[:2] in {"19", "20"}:
        return NumberToken(raw, value, "year", 0.0, year=int(raw))
    # small counts written as integers must match exactly
    tolerance = 0.0 if "." not in raw and value <= 20 else _half_unit(raw)
    return NumberToken(raw, value, "plain", tolerance)


def extract_numbers(text: str, *, words: bool = True) -> list[NumberToken]:
    found: list[NumberToken] = []
    for match in PATTERN.finditer(text):
        group = match.lastgroup
        raw = match.group(0)
        if group == "range":
            for part in (match["r1"], match["r2"]):
                found.append(NumberToken(part, float(part), "ratio", _half_unit(part)))
        elif group == "iso":
            found.append(
                NumberToken(raw, 0.0, "date", 0.0, (int(match["im"]), int(match["id"])), int(match["iy"]))
            )
        elif group in {"dmy", "mdy"}:
            month = MONTHS[(match["dmon"] or match["mmon"])[:3].lower()]
            day = int(match["dd"] or match["md"])
            year_text = match["dyear"] or match["myear"]
            found.append(
                NumberToken(raw, 0.0, "date", 0.0, (month, day), int(year_text) if year_text else None)
            )
        elif group == "ratio":
            found.append(NumberToken(raw, float(match["rv"]), "ratio", _half_unit(match["rv"])))
        elif group == "pct":
            found.append(NumberToken(raw, float(match["pv"]), "percent", _half_unit(match["pv"])))
        elif group == "abbr":
            multiplier = MULTIPLIERS[match["au"].lower()]
            value = float(match["av"]) * multiplier
            found.append(NumberToken(raw, value, "magnitude", _half_unit(match["av"], multiplier)))
        elif group in {"indian", "western"}:
            digits = raw.replace(",", "")
            found.append(NumberToken(raw, float(digits), "magnitude", _half_unit(digits)))
        elif group == "dur":
            unit = DURATION_SECONDS[match["duu"][0].lower()]
            value = float(match["duv"]) * unit
            found.append(NumberToken(raw, value, "duration", _half_unit(match["duv"], unit)))
        elif group == "plain":
            found.append(_plain_token(raw))
        elif group == "word" and words:
            word = raw.lower()
            if word in COUNT_WORDS:
                found.append(NumberToken(raw, float(COUNT_WORDS[word]), "count_word", 0.0))
            elif word in MULTIPLE_WORDS:
                found.append(NumberToken(raw, MULTIPLE_WORDS[word], "multiple_word", 0.0))
            else:
                found.append(NumberToken(raw, FRACTION_WORDS[word], "fraction", FRACTION_TOLERANCE))
    return found


def _comparable(allowed: AllowedNumber) -> float:
    return allowed.value * 60 if allowed.unit == "minutes" else allowed.value


def matches(token: NumberToken, allowed: AllowedNumber, *, rel_tol: float, multiple_tol: float) -> bool:
    units = UNITS_FOR_KIND.get(token.kind)
    if units is not None and allowed.unit not in units:
        return False
    if units is None and allowed.unit == "year":
        return False
    target = _comparable(allowed) if token.kind == "duration" else allowed.value
    tolerance = multiple_tol if token.kind == "multiple_word" else token.tolerance
    # abbreviated forms (18.4K) carry their own rounding; relative slack only helps precise forms
    limit = tolerance if tolerance >= 1 else max(tolerance, rel_tol * abs(target))
    return abs(token.value - target) <= limit


def date_allowed(token: NumberToken, allowed_dates: Iterable[date]) -> bool:
    if token.month_day is None:
        return False
    month, day = token.month_day
    return any(
        item.month == month and item.day == day and (token.year is None or token.year == item.year)
        for item in allowed_dates
    )


def compact_number(value: float) -> str:
    # deterministic display used in packs and feedback: 18.4K, 1.2M
    magnitude = abs(value)
    for threshold, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if magnitude >= threshold:
            scaled = f"{value / threshold:.1f}".removesuffix(".0")
            return f"{scaled}{suffix}"
    return f"{value:.0f}" if float(value).is_integer() else f"{value:.1f}"


def ratio_display(value: float) -> str:
    return f"{value:.1f}x"


def numbers_in(text: str) -> list[float]:
    # plain digits inside client-provided strings, e.g. "24/7 pharmacy"
    return [
        token.value for token in extract_numbers(normalize_numbers(text), words=False) if token.kind != "date"
    ]
