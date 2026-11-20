from app.core.config import RecommendRangeSettings
from app.services.recommend.expectation import INSUFFICIENT_DISPLAY, estimate_range
from tests.recommend.factories import baseline

SETTINGS = RecommendRangeSettings()
LABEL = "(indicative, calculated by GrowthHacker)"


def test_the_range_is_the_interquartile_of_plain_reference_ratios_times_the_baseline() -> None:
    estimate = estimate_range([1.2, 1.4, 1.6, 1.8, 2.0], 40, baseline(2_000), SETTINGS, 7).range

    assert (estimate.low, estimate.high) == (1.4, 1.8)
    assert estimate.views_at_age_range == (2_800, 3_600)
    assert estimate.basis == "peer" and estimate.n_peer == 40 and estimate.n_peer_effective == 5
    assert estimate.display == f"1.4x-1.8x your usual views {LABEL}"


def test_three_references_use_the_min_max_range() -> None:
    estimate = estimate_range([2.5, 1.2, 1.9], 12, baseline(), SETTINGS, 7).range

    assert (estimate.low, estimate.high) == (1.2, 2.5)


def test_each_ratio_is_capped_to_the_band_before_the_spread_is_read() -> None:
    estimate = estimate_range([4.0, 9.0, 25.0, 0.1], 20, baseline(1_000), SETTINGS, 7).range

    assert (estimate.low, estimate.high) == (2.4, 3.0)
    assert estimate.views_at_age_range == (2_400, 3_000)
    viral = estimate_range([6.0, 8.0, 12.0], 20, baseline(1_000), SETTINGS, 7).range
    assert (viral.low, viral.high) == (3.0, 3.0) and viral.display.startswith("3.0x your usual views")


def test_too_few_reference_ratios_give_no_range() -> None:
    estimate = estimate_range([3.0, 4.0], 30, baseline(), SETTINGS, 7).range

    assert estimate.basis == "insufficient"
    assert estimate.low is None and estimate.high is None
    assert estimate.display == INSUFFICIENT_DISPLAY and estimate.n_peer == 30


def test_no_client_baseline_reads_as_relative_to_the_reference_channels() -> None:
    estimate = estimate_range([2.0, 2.5, 3.0, 2.2, 2.8, 2.4], 6, None, SETTINGS, 7).range

    assert estimate.basis == "peer_only_no_baseline"
    assert estimate.display.startswith("no baseline yet; the reference videos ran ")
    assert estimate.display.endswith(LABEL) and estimate.views_at_age_range is None


def test_provisional_client_baseline_is_flagged() -> None:
    estimate = estimate_range([2.0] * 5, 10, baseline(n=4), SETTINGS, 7)

    assert "provisional_baseline" in estimate.flags
