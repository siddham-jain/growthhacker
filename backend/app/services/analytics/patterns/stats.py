"""closed-form interval and test statistics on our own counts; no scipy."""

import math
from collections.abc import Sequence
from fractions import Fraction

Z95 = 1.959964


def wilson(successes: int, n: int, z: float = Z95) -> tuple[float, float]:
    # wilson (1927) score interval; recommended over wald by brown, cai and dasgupta (2001)
    if n <= 0:
        return (0.0, 1.0)
    if not 0 <= successes <= n:
        raise ValueError("successes must lie in [0, n]")
    p = successes / n
    z2 = z * z
    centre = p + z2 / (2 * n)
    half = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))
    denominator = 1 + z2 / n
    # the closed form leaves rounding residue at the edges; 0 and n successes pin the bound exactly
    low = 0.0 if successes == 0 else min(p, max(0.0, (centre - half) / denominator))
    high = 1.0 if successes == n else max(p, min(1.0, (centre + half) / denominator))
    return (low, high)


def katz_rr_ci(a: int, b: int, c: int, d: int, z: float = Z95) -> tuple[float, float] | None:
    # katz et al. (1978) log interval for the risk ratio; haldane-anscombe 0.5 when any cell is zero
    if a + b == 0 or c + d == 0:
        return None
    fa, fb, fc, fd = (float(x) for x in (a, b, c, d))
    if 0 in (a, b, c, d):
        fa, fb, fc, fd = fa + 0.5, fb + 0.5, fc + 0.5, fd + 0.5
    rr = (fa / (fa + fb)) / (fc / (fc + fd))
    se = math.sqrt(1 / fa - 1 / (fa + fb) + 1 / fc - 1 / (fc + fd))
    return (math.exp(math.log(rr) - z * se), math.exp(math.log(rr) + z * se))


def fisher_one_sided(a: int, b: int, c: int, d: int) -> float:
    # p of observing at least a outliers among the n with the trait (hypergeometric upper tail)
    total = a + b + c + d
    outliers, with_trait = a + c, a + b
    if total == 0:
        return 1.0
    denominator = math.comb(total, with_trait)
    upper = min(outliers, with_trait)
    numerator = sum(
        math.comb(outliers, x) * math.comb(total - outliers, with_trait - x) for x in range(a, upper + 1)
    )
    return float(Fraction(numerator, denominator))


def bh_qvalues(p_values: Sequence[float]) -> list[float]:
    # benjamini and hochberg (1995) step-up adjusted values, returned in input order
    m = len(p_values)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: (p_values[i], i))
    adjusted = [0.0] * m
    running = 1.0
    for rank in range(m, 0, -1):
        index = order[rank - 1]
        running = min(running, p_values[index] * m / rank)
        adjusted[index] = min(1.0, running)
    return adjusted


def shrink(successes: float, n: float, prior_mean: float, alpha: float) -> float:
    # beta prior with alpha pseudo-observations centred on the scope rate
    if n + alpha == 0:
        return prior_mean
    return (successes + alpha * prior_mean) / (n + alpha)


def quantiles(values: Sequence[float], cap: float | None = None) -> tuple[float, float, float]:
    if not values:
        return (0.0, 0.0, 0.0)
    data = sorted(min(v, cap) if cap is not None else v for v in values)
    return (_percentile(data, 25), _percentile(data, 50), _percentile(data, 75))


def _percentile(sorted_values: Sequence[float], pct: float) -> float:
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    position = (len(sorted_values) - 1) * pct / 100
    low = math.floor(position)
    high = min(low + 1, len(sorted_values) - 1)
    fraction = position - low
    return float(sorted_values[low] + (sorted_values[high] - sorted_values[low]) * fraction)


def median(values: Sequence[float]) -> float | None:
    return _percentile(sorted(values), 50) if values else None


def cohen_kappa(left: Sequence[bool], right: Sequence[bool]) -> float | None:
    if len(left) != len(right) or not left:
        return None
    n = len(left)
    observed = sum(a == b for a, b in zip(left, right, strict=True)) / n
    p_left, p_right = sum(left) / n, sum(right) / n
    expected = p_left * p_right + (1 - p_left) * (1 - p_right)
    if expected == 1:
        return 1.0 if observed == 1 else 0.0
    return (observed - expected) / (1 - expected)


def _ranks(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start
        while end + 1 < len(order) and values[order[end + 1]] == values[order[start]]:
            end += 1
        for position in range(start, end + 1):
            ranks[order[position]] = (start + end) / 2 + 1
        start = end + 1
    return ranks


def spearman_rho(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    rl, rr = _ranks(left), _ranks(right)
    mean_l, mean_r = sum(rl) / len(rl), sum(rr) / len(rr)
    cov = sum((a - mean_l) * (b - mean_r) for a, b in zip(rl, rr, strict=True))
    var_l = sum((a - mean_l) ** 2 for a in rl)
    var_r = sum((b - mean_r) ** 2 for b in rr)
    if var_l == 0 or var_r == 0:
        return None
    return cov / math.sqrt(var_l * var_r)
