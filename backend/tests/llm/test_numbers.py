import random

import pytest

from app.services.llm.writer.numbers import (
    AllowedNumber,
    compact_number,
    extract_numbers,
    mask_spans,
    matches,
    normalize_numbers,
)

REL_TOL = 0.005
MULTIPLE_TOL = 0.25


def accepted(text: str, value: float, unit: str) -> bool:
    tokens = extract_numbers(normalize_numbers(text))
    assert tokens, f"no number found in {text!r}"
    allowed = AllowedNumber(value=value, unit=unit, display="")
    return all(matches(token, allowed, rel_tol=REL_TOL, multiple_tol=MULTIPLE_TOL) for token in tokens)


@pytest.mark.parametrize(
    "text",
    ["18400 views", "18,400 views", "18.4K views", "18.4k views", "18.4 thousand views", "18K views"],
)
def test_view_count_display_variants_match(text: str) -> None:
    assert accepted(text, 18_400, "views")


@pytest.mark.parametrize("text", ["1.84 lakh views", "1,84,000 views", "184K views", "184,000 views"])
def test_indian_and_western_grouping_match(text: str) -> None:
    assert accepted(text, 184_000, "views")


def test_crore_and_devanagari_digits() -> None:
    assert accepted("1.2 crore views", 12_000_000, "views")
    assert accepted("४५ videos", 45, "videos")


@pytest.mark.parametrize(
    ("text", "value", "ok"),
    [
        ("18.4K", 18_449, True),
        ("18.4K", 18_451, False),
        ("18.5K", 18_400, False),
        ("18K", 18_600, False),
        ("3.6x", 3.62, True),
        ("about 4x", 3.62, True),
        ("5x", 3.62, False),
    ],
)
def test_rounding_boundaries(text: str, value: float, ok: bool) -> None:
    unit = "ratio_x" if "x" in text else "views"
    assert accepted(text, value, unit) is ok


def test_small_counts_are_exact() -> None:
    assert accepted("4 of the channels", 4, "channels")
    assert not accepted("5 of the channels", 4, "channels")


def test_one_is_a_count_like_in_the_recommend_gate() -> None:
    assert accepted("one of the channels", 1, "channels")
    assert not accepted("one of the channels", 4, "channels")


def test_multiple_words_use_their_tolerance() -> None:
    assert accepted("twice the baseline", 2.1, "ratio_x")
    assert not accepted("twice the baseline", 2.4, "ratio_x")


def test_units_must_be_compatible() -> None:
    assert not accepted("45%", 45, "views")
    assert not accepted("3.6x", 3.6, "percent")
    assert accepted("a 60-second short", 1, "minutes")


def test_derived_percent_is_not_a_count() -> None:
    assert not accepted("67% of outliers", 4, "count")


def test_dates_and_years_are_typed() -> None:
    tokens = extract_numbers(normalize_numbers("Published 3 Nov 2026, again on Nov 5 and in 2025"))
    assert [(t.kind, t.month_day, t.year) for t in tokens] == [
        ("date", (11, 3), 2026),
        ("date", (11, 5), None),
        ("year", None, 2025),
    ]


def test_ranges_yield_both_ends() -> None:
    tokens = extract_numbers(normalize_numbers("expect 1.2x–2.0x"))
    assert [(t.kind, t.value) for t in tokens] == [("ratio", 1.2), ("ratio", 2.0)]


def test_masking_removes_numbers_inside_allowed_names() -> None:
    text = normalize_numbers("Like 5-Minute Crafts and the 24/7 Pharmacy team")
    masked = mask_spans(text, ["5-Minute Crafts", "24/7 Pharmacy"])
    assert extract_numbers(masked) == []


def test_alphanumeric_tokens_are_not_numbers() -> None:
    assert extract_numbers("H1N1 and v1 and f2") == []


def test_compact_display_always_matches_its_value() -> None:
    generator = random.Random(7)
    for _ in range(500):
        value = generator.randint(1_000, 50_000_000)
        assert accepted(compact_number(value), value, "views"), (value, compact_number(value))


def test_values_outside_tolerance_never_match() -> None:
    generator = random.Random(11)
    for _ in range(300):
        value = generator.randint(10_000, 900_000)
        display = compact_number(value)
        assert not accepted(display, value * 1.2, "views")
