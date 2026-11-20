import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date

NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
}
SCALES = {
    "k": 1e3,
    "thousand": 1e3,
    "M": 1e6,
    "million": 1e6,
    "lakh": 1e5,
    "lakhs": 1e5,
    "crore": 1e7,
    "crores": 1e7,
}
MONTHS = {
    name: index
    for index, names in enumerate(
        (
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ),
        start=1,
    )
    for name in names
}
_MONTH = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"
ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
DAY_MONTH = re.compile(rf"\b(\d{{1,2}})\s+{_MONTH}(?:,?\s+(\d{{4}}))?\b", re.IGNORECASE)
MONTH_DAY = re.compile(rf"\b{_MONTH}\s+(\d{{1,2}})(?:,?\s+(\d{{4}}))?\b", re.IGNORECASE)
NUMBER = re.compile(
    r"(?<![\w.,])(\d{1,3}(?:,\d{2,3})+|\d+(?:\.\d+)?)"
    r"(?:\s?(k|M|thousand|million|lakhs?|crores?)\b)?(\s?%|\s?[x×](?![a-wyz]))?(st|nd|rd|th)?",
)
WORD = re.compile(r"\b(" + "|".join(NUMBER_WORDS) + r")\b", re.IGNORECASE)
INTEGER_TOLERANCE = 0.05


@dataclass(frozen=True, slots=True)
class WrittenNumber:
    text: str
    value: float
    decimals: int
    scale: float
    percent: bool


@dataclass(slots=True)
class AllowedNumbers:
    values: set[float] = field(default_factory=set)
    fractions: set[tuple[float, float]] = field(default_factory=set)
    dates: set[date] = field(default_factory=set)

    def add(self, value: float | int | None) -> None:
        if value is not None and not isinstance(value, bool):
            self.values.add(float(value))

    def add_text(self, text: str) -> None:
        # numbers inside code-built labels such as "3–8m videos" or a client's own topic text
        for written in _numbers(_strip_dates(text)[1]):
            self.values.add(written.value * written.scale)

    def add_fraction(self, part: float | int | str | None, whole: float | int | str | None) -> None:
        if isinstance(part, int | float) and isinstance(whole, int | float) and whole:
            self.fractions.add((float(part), float(whole)))

    def allows(self, written: WrittenNumber) -> bool:
        if written.percent:
            candidates = [100 * part / whole for part, whole in self.fractions]
            candidates += [value * 100 for value in self.values if 0 < value <= 1]
            candidates += list(self.values)
            return any(round(candidate, written.decimals) == written.value for candidate in candidates)
        return any(_matches(value, written.value, written.decimals, written.scale) for value in self.values)

    def allows_date(self, day: int, month: int, year: int | None) -> bool:
        return any(
            item.day == day and item.month == month and (year is None or item.year == year)
            for item in self.dates
        )


def _matches(allowed: float, written: float, decimals: int, scale: float) -> bool:
    scaled = allowed / scale
    if round(scaled, decimals) != round(written, decimals):
        return False
    # "4x" must not pass for 3.6; coarse rounding is only fine for abbreviated large numbers
    return scale > 1 or decimals > 0 or abs(scaled - written) < INTEGER_TOLERANCE


def _strip_dates(text: str) -> tuple[list[tuple[int, int, int | None]], str]:
    found: list[tuple[int, int, int | None]] = []

    def iso(match: re.Match[str]) -> str:
        found.append((int(match.group(3)), int(match.group(2)), int(match.group(1))))
        return " "

    def day_month(match: re.Match[str]) -> str:
        year = match.group(3)
        found.append(
            (int(match.group(1)), MONTHS[match.group(2).lower().rstrip(".")], int(year) if year else None)
        )
        return " "

    def month_day(match: re.Match[str]) -> str:
        year = match.group(3)
        found.append(
            (int(match.group(2)), MONTHS[match.group(1).lower().rstrip(".")], int(year) if year else None)
        )
        return " "

    text = ISO_DATE.sub(iso, text)
    text = DAY_MONTH.sub(day_month, text)
    text = MONTH_DAY.sub(month_day, text)
    return found, text


def _numbers(text: str) -> list[WrittenNumber]:
    numbers = []
    for match in NUMBER.finditer(text):
        raw, scale_word, suffix, _ordinal = match.groups()
        digits = raw.replace(",", "")
        decimals = len(digits.partition(".")[2])
        numbers.append(
            WrittenNumber(
                text=match.group(0).strip(),
                value=float(digits),
                decimals=decimals,
                scale=SCALES.get(scale_word, SCALES.get((scale_word or "").lower(), 1.0))
                if scale_word
                else 1.0,
                percent=bool(suffix and "%" in suffix),
            )
        )
    for match in WORD.finditer(text):
        numbers.append(
            WrittenNumber(
                text=match.group(0),
                value=float(NUMBER_WORDS[match.group(1).lower()]),
                decimals=0,
                scale=1.0,
                percent=False,
            )
        )
    return numbers


def unsupported_numbers(texts: Iterable[str], allowed: AllowedNumbers) -> list[str]:
    """every number written in the card must exist in the evidence at the precision written."""
    offending: list[str] = []
    for text in texts:
        dates, remainder = _strip_dates(text)
        for day, month, year in dates:
            if not allowed.allows_date(day, month, year):
                offending.append(f"{day}/{month}/{year or ''}".rstrip("/"))
        for written in _numbers(remainder):
            if not allowed.allows(written):
                offending.append(written.text)
    return offending
