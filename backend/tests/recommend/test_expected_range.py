import math

from app.core.config import RecommendRangeSettings
from app.services.recommend.expectation import INSUFFICIENT_DISPLAY, estimate_range
from tests.recommend.factories import baseline, evidence

SETTINGS = RecommendRangeSettings()


def peers(ratios: list[float], baseline_views: float = 2_000):
    return [evidence(index, ratio, baseline=baseline_views) for index, ratio in enumerate(ratios)]


def test_small_samples_shrink_toward_the_clients_normal() -> None:
    small = estimate_range(peers([2.0, 2.5, 3.0, 2.2, 2.8, 2.4]), [], baseline(), SETTINGS, 7).range
    large = estimate_range(peers([2.0, 2.5, 3.0, 2.2, 2.8, 2.4] * 20), [], baseline(), SETTINGS, 7).range

    assert small.basis == "peer" and large.basis == "peer"
    assert small.high < large.high
    assert 1.0 <= small.low <= small.high


def test_large_samples_approach_the_raw_interquartile_range() -> None:
    ratios = [1.2, 1.4, 1.6, 1.8, 2.0] * 200
    estimate = estimate_range(peers(ratios), [], baseline(), SETTINGS, 7).range

    # quartiles are 1.4x and 1.8x; display floors the low bound and ceils the high bound to 1 dp
    assert estimate.low == 1.3
    assert estimate.high == 1.8


def test_too_few_comparable_videos_gives_no_range() -> None:
    estimate = estimate_range(peers([3.0, 4.0, 5.0]), [], baseline(), SETTINGS, 7).range

    assert estimate.basis == "insufficient"
    assert estimate.low is None and estimate.high is None
    assert estimate.display == INSUFFICIENT_DISPLAY


def test_extreme_ratios_are_capped_and_the_range_has_a_minimum_width() -> None:
    capped = estimate_range(peers([20.0] * 400), [], baseline(), SETTINGS, 7).range
    flat = estimate_range(peers([1.0] * 400), [], baseline(), SETTINGS, 7).range

    assert capped.high == 3.0
    assert math.log(flat.high) - math.log(flat.low) >= math.log(SETTINGS.min_width) - 0.1
    assert flat.display.endswith("(indicative)")


def test_peers_far_from_the_clients_size_cap_the_range_at_two() -> None:
    estimate = estimate_range(peers([5.0] * 60, baseline_views=400_000), [], baseline(2_000), SETTINGS, 7)

    assert estimate.range.size_note == "peers_much_larger"
    assert estimate.range.high <= 2.0
    assert "size_mismatch" in estimate.flags


def test_no_client_baseline_reads_as_peer_relative() -> None:
    estimate = estimate_range(peers([2.0, 2.5, 3.0, 2.2, 2.8, 2.4]), [], None, SETTINGS, 7).range

    assert estimate.basis == "peer_only_no_baseline"
    assert estimate.display.startswith("no baseline yet; similar videos ran ")
    assert estimate.views_at_age_range is None


def test_own_history_blends_in_and_takes_over_after_twenty_videos() -> None:
    blended = estimate_range(peers([3.0] * 30), [0.8, 0.9, 1.0, 0.9], baseline(), SETTINGS, 7).range
    own_only = estimate_range(peers([3.0] * 30), [1.0] * 25, baseline(), SETTINGS, 7).range
    peer_only = estimate_range(peers([3.0] * 30), [], baseline(), SETTINGS, 7).range

    assert blended.basis == "blended" and blended.high < peer_only.high
    assert own_only.basis == "own"
    low_views, high_views = blended.views_at_age_range
    assert 2_000 * blended.low <= low_views < 2_000 * (blended.low + 0.1)
    assert 2_000 * (blended.high - 0.1) < high_views <= 2_000 * blended.high


def test_provisional_client_baseline_is_flagged() -> None:
    estimate = estimate_range(peers([2.0] * 10), [], baseline(n=4), SETTINGS, 7)

    assert "provisional_baseline" in estimate.flags
