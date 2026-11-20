from datetime import date

import pytest

from app.core.config import RecommendNoCopySettings, RecommendSettings
from app.services.recommend.text.copying import CopyDetector
from app.services.recommend.text.lexicon import Lexicon
from app.services.recommend.text.numbers import AllowedNumbers, unsupported_numbers


def allowed(
    *values: float, fractions: tuple[tuple[int, int], ...] = (), dates: tuple[date, ...] = ()
) -> AllowedNumbers:
    numbers = AllowedNumbers()
    for value in values:
        numbers.add(value)
    for part, whole in fractions:
        numbers.add_fraction(part, whole)
    numbers.dates.update(dates)
    return numbers


@pytest.mark.parametrize(
    ("text", "evidence"),
    [
        ("ran 18.4k views by day 7", allowed(18_437, 7)),
        ("18,400 views", allowed(18_400)),
        ("1.2 lakh views", allowed(120_400)),
        ("a median 3.6x their normal", allowed(3.62)),
        ("3.6× their normal", allowed(3.6)),
        ("4 of 6 outliers", allowed(4, 6)),
        ("67% of outliers", allowed(4, 6, fractions=((4, 6),))),
        ("four outliers", allowed(4)),
        ("in 2026", allowed(2026)),
        ("3–8 min videos", allowed(3, 8)),
        ("1.2x-2.0x (indicative)", allowed(1.2, 2.0)),
        ("published 2 Nov 2026", allowed(dates=(date(2026, 11, 2),))),
        ("published 2026-11-02", allowed(dates=(date(2026, 11, 2),))),
    ],
)
def test_numbers_written_from_evidence_pass(text: str, evidence: AllowedNumbers) -> None:
    assert unsupported_numbers([text], evidence) == []


@pytest.mark.parametrize(
    ("text", "evidence", "offending"),
    [
        ("a median 4x their normal", allowed(3.6), "4x"),
        ("18,437 views", allowed(18_400), "18,437"),
        ("67% of outliers", allowed(4, 6), "67%"),
        ("five outliers", allowed(4), "five"),
        ("published 3 Nov 2026", allowed(dates=(date(2026, 11, 2),)), "3/11/2026"),
        ("7 of 9 outliers", allowed(7), "9"),
    ],
)
def test_invented_or_rounded_numbers_fail(text: str, evidence: AllowedNumbers, offending: str) -> None:
    assert offending in unsupported_numbers([text], evidence)


def detector(others: list[str], own: list[str] | None = None) -> CopyDetector:
    return CopyDetector(others, own or [], RecommendNoCopySettings())


def test_exact_and_near_copies_of_competitor_titles_are_caught() -> None:
    copy = detector(["Knee Replacement Cost in India Explained | Fortis"])

    assert copy.check("knee replacement cost in india explained fortis").rule == "exact"
    assert copy.check("Knee replacement costs in India explained by Fortis").rule == "ratio"
    assert copy.check("Explained: knee replacement cost in India | Fortis").rule == "token_sort_ratio"


def test_short_topic_phrase_inside_a_longer_title_is_not_a_copy() -> None:
    # token_set_ratio would score this 100; the gate deliberately does not use it
    copy = detector(["Dental implant cost in Delhi | full breakdown and EMI options"])

    assert copy.check("Dental implant cost") is None


def test_a_verbatim_six_token_chunk_lifted_from_a_long_title_is_caught() -> None:
    copy = detector(["Doctor explains what really happens during a knee replacement surgery step by step"])

    assert copy.check("what really happens during a knee replacement").rule == "partial_ratio"
    assert copy.check("during a knee replacement") is None


def test_repeating_the_clients_own_recent_title_uses_the_stricter_threshold() -> None:
    copy = detector([], own=["Knee pain causes and care"])

    assert copy.check("Knee pain: causes and care tips").rule == "own_ratio"
    assert copy.check("Robotic knee surgery explained") is None


def test_policy_lexicon_targets_channel_income_and_demographics_not_prices() -> None:
    settings = RecommendSettings()
    lexicon = Lexicon(settings.policy_lexicon)

    assert [hit.pattern_id for hit in lexicon.find(["This channel earns a big CPM"])] == ["channel_income"]
    assert [hit.pattern_id for hit in lexicon.find(["Their viewers are mostly young parents"])] == [
        "audience_makeup"
    ]
    assert lexicon.find(["How much does a knee replacement cost? Prices start at ₹2 lakh"]) == []


def test_healthcare_pack_blocks_outcome_promises() -> None:
    lexicon = Lexicon(RecommendSettings().healthcare_lexicon)

    assert lexicon.find(["Robotic surgery cures knee pain"])
    assert lexicon.find(["A painless, risk-free procedure"])
    assert lexicon.find(["What recovery looks like after surgery"]) == []
