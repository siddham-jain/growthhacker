import math
from fractions import Fraction

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.services.analytics.baselines import summarise
from app.services.analytics.patterns.stats import (
    bh_qvalues,
    cohen_kappa,
    fisher_one_sided,
    katz_rr_ci,
    quantiles,
    shrink,
    spearman_rho,
    wilson,
)

counts = st.integers(min_value=0, max_value=60)


def test_wilson_matches_the_reference_interval():
    low, high = wilson(3, 10)
    assert low == pytest.approx(0.108, abs=5e-4)
    assert high == pytest.approx(0.603, abs=5e-4)


def test_wilson_with_no_trials_is_uninformative():
    assert wilson(0, 0) == (0.0, 1.0)


def test_katz_interval_on_a_textbook_table():
    # 20/100 exposed vs 10/100 unexposed: rr 2.0, log se sqrt(1/20 - 1/100 + 1/10 - 1/100)
    low, high = katz_rr_ci(20, 80, 10, 90)
    se = math.sqrt(1 / 20 - 1 / 100 + 1 / 10 - 1 / 100)
    assert low == pytest.approx(2.0 * math.exp(-1.959964 * se))
    assert high == pytest.approx(2.0 * math.exp(1.959964 * se))
    assert low == pytest.approx(0.9866, abs=1e-3)
    assert high == pytest.approx(4.0546, abs=1e-3)


def test_katz_uses_haldane_correction_on_zero_cells():
    low, high = katz_rr_ci(3, 5, 0, 12)
    assert 0 < low < high
    assert katz_rr_ci(0, 0, 3, 4) is None


def _hypergeometric_tail(a: int, b: int, c: int, d: int) -> Fraction:
    total, outliers, with_trait = a + b + c + d, a + c, a + b
    terms = []
    for x in range(a, with_trait + 1):
        if x > outliers or with_trait - x > total - outliers:
            continue
        terms.append(Fraction(math.comb(outliers, x) * math.comb(total - outliers, with_trait - x)))
    return sum(terms, Fraction(0)) / math.comb(total, with_trait)


def test_fisher_matches_a_hand_computed_tail():
    # 3 of 4 with the trait are outliers, 1 of 10 without: p = (C(4,3)C(10,1) + C(4,4)C(10,0)) / C(14,4)
    assert fisher_one_sided(3, 1, 1, 9) == pytest.approx((4 * 10 + 1) / math.comb(14, 4))


def test_benjamini_hochberg_worked_example():
    p = [0.01, 0.04, 0.03, 0.005]
    assert bh_qvalues(p) == pytest.approx([0.02, 0.04, 0.04, 0.02])


def test_benjamini_hochberg_paper_example_rejects_four_at_five_percent():
    # the fifteen p-values of benjamini and hochberg (1995), section 4
    p = [
        0.0001,
        0.0004,
        0.0019,
        0.0095,
        0.0201,
        0.0278,
        0.0298,
        0.0344,
        0.0459,
        0.3240,
        0.4262,
        0.5719,
        0.6528,
        0.7590,
        1.0,
    ]
    assert sum(q <= 0.05 for q in bh_qvalues(p)) == 4


def test_shrinkage_reduces_to_raw_rate_without_prior():
    assert shrink(3, 10, 0.5, 0) == pytest.approx(0.3)
    assert shrink(3, 10, 0.1, 5) == pytest.approx((3 + 0.5) / 15)


def test_median_and_mad_are_robust_to_one_spike():
    summary = summarise([1_000, 1_100, 1_200, 1_300, 900_000])
    assert summary.median == 1_200
    assert summary.mean > 100_000
    assert summary.mad_log10 < 0.1


def test_agreement_statistics():
    assert cohen_kappa([True, False, True, False], [True, False, True, False]) == pytest.approx(1.0)
    assert spearman_rho([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert spearman_rho([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)


@settings(max_examples=200, deadline=None)
@given(st.integers(min_value=1, max_value=500).flatmap(lambda n: st.tuples(st.integers(0, n), st.just(n))))
def test_wilson_is_a_valid_interval_around_the_estimate(case):
    successes, n = case
    low, high = wilson(successes, n)
    assert 0.0 <= low <= successes / n <= high <= 1.0


@settings(max_examples=200, deadline=None)
@given(counts, counts, counts, counts)
def test_fisher_is_an_exact_probability(a, b, c, d):
    if a + b + c + d == 0:
        return
    p = fisher_one_sided(a, b, c, d)
    assert 0.0 <= p <= 1.0 + 1e-12
    assert p == pytest.approx(float(_hypergeometric_tail(a, b, c, d)))


@settings(max_examples=150, deadline=None)
@given(st.integers(1, 30), st.integers(0, 30), st.integers(0, 30), st.integers(1, 30))
def test_fisher_falls_as_more_outliers_carry_the_trait(a, b, c, d):
    # moving one trait video from non-outlier to outlier can only make the tail rarer
    if b == 0:
        return
    assert fisher_one_sided(a + 1, b - 1, c, d) <= fisher_one_sided(a, b, c, d) + 1e-12


@settings(max_examples=200, deadline=None)
@given(st.integers(1, 40), st.integers(0, 40), st.integers(1, 40), st.integers(0, 40))
def test_katz_interval_contains_the_point_estimate(a, b, c, d):
    low, high = katz_rr_ci(a, b, c, d)
    fa, fb, fc, fd = (x + 0.5 for x in (a, b, c, d)) if 0 in (b, d) else (a, b, c, d)
    estimate = (fa / (fa + fb)) / (fc / (fc + fd))
    assert low <= estimate <= high


@settings(max_examples=200, deadline=None)
@given(st.lists(st.floats(min_value=0.0, max_value=1.0, allow_nan=False), min_size=1, max_size=60))
def test_bh_qvalues_are_bounded_and_order_preserving(p_values):
    q = bh_qvalues(p_values)
    assert all(p <= qv + 1e-12 and qv <= 1.0 for p, qv in zip(p_values, q, strict=True))
    order = sorted(range(len(p_values)), key=lambda i: p_values[i])
    assert all(q[order[i]] <= q[order[i + 1]] + 1e-12 for i in range(len(order) - 1))


@settings(max_examples=200, deadline=None)
@given(st.integers(0, 50), st.integers(0, 50), st.floats(0.0, 1.0), st.floats(0.0, 20.0))
def test_shrinkage_lies_between_raw_rate_and_prior(k, extra, prior, alpha):
    n = k + extra
    value = shrink(k, n, prior, alpha)
    raw = k / n if n else prior
    assert min(raw, prior) - 1e-12 <= value <= max(raw, prior) + 1e-12


@settings(max_examples=200, deadline=None)
@given(st.lists(st.floats(min_value=0.0, max_value=1e7, allow_nan=False), min_size=1, max_size=80))
def test_baseline_summary_is_ordered_and_robust(values):
    summary = summarise(values)
    assert summary.p25 <= summary.median <= summary.p75
    slack = 1e-9 * max(1.0, max(values))
    assert min(values) - slack <= summary.trimmed_mean_10 <= max(values) + slack
    assert summary.mad_log10 >= 0
    low, mid, high = quantiles(values, cap=50.0)
    assert low <= mid <= high <= 50.0
