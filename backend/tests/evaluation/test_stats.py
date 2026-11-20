import numpy as np
import pytest

from app.evaluation.stats.agreement import (
    cohen_kappa,
    gwet_ac1,
    krippendorff_alpha,
    percent_agreement,
    weighted_kappa,
)
from app.evaluation.stats.bootstrap import item_bootstrap, stratified_cluster_bootstrap
from app.evaluation.stats.inference import empirical_p, holm, spearman
from app.evaluation.stats.proportions import (
    minimum_detectable_lift,
    newcombe_difference,
    one_sample_size,
    two_proportion_sample_size,
    wilson,
)
from app.evaluation.stats.sampling import f_beta, weighted_confusion, weighted_precision_recall

# krippendorff (2011) worked example: 4 coders, 12 units, missing values as none
KRIPPENDORFF_UNITS = [
    [1, 1, None, 1],
    [2, 2, 3, 2],
    [3, 3, 3, 3],
    [3, 3, 3, 3],
    [2, 2, 2, 2],
    [1, 2, 3, 4],
    [4, 4, 4, 4],
    [1, 1, 2, 1],
    [2, 2, 2, 2],
    [None, 5, 5, 5],
    [None, None, 1, 1],
    [None, 3, None, None],
]


def test_wilson_matches_reference_values() -> None:
    # brown, cai & dasgupta (2001): x = 0 of n = 10 gives an upper bound near 0.278
    low, high = wilson(0, 10)
    assert low == 0.0
    assert high == pytest.approx(0.2775, abs=1e-3)
    low, high = wilson(5, 10)
    assert (low, high) == pytest.approx((0.2366, 0.7634), abs=1e-3)
    assert wilson(0, 0) == (0.0, 1.0)


def test_newcombe_difference_brackets_the_estimate() -> None:
    # newcombe (1998) example 56/70 vs 48/80: delta 0.20, interval [0.0524, 0.3339]
    delta, low, high = newcombe_difference(56, 70, 48, 80)
    assert delta == pytest.approx(0.2)
    assert (low, high) == pytest.approx((0.0524, 0.3339), abs=1e-3)


def test_sample_sizes_match_the_plan_table() -> None:
    assert two_proportion_sample_size(0.10, 0.20) == 199
    assert two_proportion_sample_size(0.10, 0.30) == 62
    assert two_proportion_sample_size(0.15, 0.30) == 121
    assert one_sample_size(0.10, 0.20) == 86


def test_minimum_detectable_lift_is_consistent_with_sample_size() -> None:
    lift = minimum_detectable_lift(0.10, 62)
    assert lift == pytest.approx(3.0, abs=0.05)
    assert minimum_detectable_lift(0.10, 2) is None
    assert minimum_detectable_lift(0.0, 100) is None


def test_cohen_kappa_and_ac1_on_textbook_table() -> None:
    # 2x2 table yes/yes 20, yes/no 5, no/yes 10, no/no 15
    a = ["y"] * 25 + ["n"] * 25
    b = ["y"] * 20 + ["n"] * 5 + ["y"] * 10 + ["n"] * 15
    assert percent_agreement(a, b) == pytest.approx(0.7)
    assert cohen_kappa(a, b) == pytest.approx(0.4)
    assert gwet_ac1(a, b) == pytest.approx(0.205 / 0.505)


def test_kappa_paradox_is_why_ac1_is_reported() -> None:
    a = ["n"] * 95 + ["y"] * 5
    b = ["n"] * 94 + ["y"] + ["y"] * 4 + ["n"]
    assert cohen_kappa(a, b) < 0.8 < gwet_ac1(a, b)


def test_weighted_kappa_penalises_far_disagreements_more() -> None:
    a = [1, 2, 3, 4, 5, 1, 2, 3]
    near = [1, 2, 3, 4, 4, 1, 2, 3]
    far = [1, 2, 3, 4, 1, 1, 2, 3]
    assert weighted_kappa(a, near, [1, 2, 3, 4, 5], "quadratic") > weighted_kappa(
        a, far, [1, 2, 3, 4, 5], "quadratic"
    )
    assert weighted_kappa(a, a, [1, 2, 3, 4, 5], "linear") == pytest.approx(1.0)


@pytest.mark.parametrize(("level", "expected"), [("nominal", 0.743), ("ordinal", 0.815), ("interval", 0.849)])
def test_krippendorff_alpha_worked_example(level: str, expected: float) -> None:
    assert krippendorff_alpha(KRIPPENDORFF_UNITS, level) == pytest.approx(expected, abs=1e-3)


def test_krippendorff_alpha_undefined_without_variation() -> None:
    assert krippendorff_alpha([[3, 3], [3, 3]], "ordinal") is None
    assert krippendorff_alpha([[3, None]], "ordinal") is None


def test_weighted_precision_recall_on_hand_computed_design() -> None:
    gold = [True, True, False, False, True]
    predicted = [True, False, True, False, True]
    weights = [2.0, 4.0, 1.0, 8.0, 2.0]
    precision, recall = weighted_precision_recall(gold, predicted, weights)
    assert precision == pytest.approx(4 / 5)
    assert recall == pytest.approx(4 / 8)
    raw = weighted_confusion(gold, predicted)
    assert (raw.tp, raw.fp, raw.fn, raw.tn) == (2, 1, 1, 1)
    assert f_beta(0.8, 0.5, 0.5) == pytest.approx(1.25 * 0.4 / (0.25 * 0.8 + 0.5))


def test_holm_and_empirical_p() -> None:
    assert holm([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])
    assert empirical_p(5.0, [1.0, 2.0, 6.0]) == pytest.approx(2 / 4)
    assert empirical_p(10.0, [1.0] * 999) == pytest.approx(1 / 1000)


def test_spearman_handles_ties_and_constants() -> None:
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert spearman([1, 2, 2, 3], [3, 2, 2, 1]) == pytest.approx(-1.0)
    assert spearman([1, 1, 1], [1, 2, 3]) is None


def test_cluster_bootstrap_is_deterministic_and_paired() -> None:
    clusters = [f"c{i // 4}" for i in range(40)]
    strata = ["s1" if i < 20 else "s2" for i in range(40)]
    values = np.arange(40, dtype=float)

    def run(seed: int) -> dict:
        return stratified_cluster_bootstrap(
            {
                "mean": lambda idx: float(values[idx].mean()),
                "double": lambda idx: float(2 * values[idx].mean()),
            },
            strata,
            clusters,
            500,
            np.random.default_rng(seed),
        )

    first, second = run(7), run(7)
    assert first["mean"].ci == second["mean"].ci
    assert first["mean"].estimate == pytest.approx(19.5)
    assert np.allclose(first["double"].samples, 2 * first["mean"].samples)


def test_item_bootstrap_ci_contains_the_estimate() -> None:
    scores = np.array([4.0, 4.5, 3.5, 5.0, 4.0, 4.5])
    result = item_bootstrap(
        {"mean": lambda idx: float(scores[idx].mean())}, 6, 1000, np.random.default_rng(1)
    )
    low, high = result["mean"].ci
    assert low <= result["mean"].estimate <= high


def test_bootstrap_coverage_is_near_nominal() -> None:
    rng = np.random.default_rng(42)
    covered = 0
    for _ in range(300):
        sample = rng.random(80) < 0.2
        result = item_bootstrap({"rate": lambda idx, s=sample: float(s[idx].mean())}, 80, 400, rng)
        low, high = result["rate"].ci
        covered += low <= 0.2 <= high
    assert 0.90 <= covered / 300 <= 0.98
