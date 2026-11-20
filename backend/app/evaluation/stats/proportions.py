import math
from statistics import NormalDist

_NORMAL = NormalDist()


def z_quantile(p: float) -> float:
    return _NORMAL.inv_cdf(p)


def wilson(successes: int, n: int, alpha: float = 0.05) -> tuple[float, float]:
    """wilson score interval (wilson 1927; brown, cai & dasgupta 2001)."""
    if n <= 0:
        return 0.0, 1.0
    if not 0 <= successes <= n:
        raise ValueError("successes must lie in [0, n]")
    z = z_quantile(1 - alpha / 2)
    p = successes / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return max(0.0, centre - half), min(1.0, centre + half)


def newcombe_difference(
    x1: int, n1: int, x2: int, n2: int, alpha: float = 0.05
) -> tuple[float, float, float]:
    """p1 - p2 with newcombe's hybrid score interval (newcombe 1998, method 10)."""
    if n1 <= 0 or n2 <= 0:
        raise ValueError("both groups need at least one observation")
    p1, p2 = x1 / n1, x2 / n2
    l1, u1 = wilson(x1, n1, alpha)
    l2, u2 = wilson(x2, n2, alpha)
    delta = p1 - p2
    low = delta - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2)
    high = delta + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)
    return delta, low, high


def two_proportion_sample_size(p1: float, p2: float, alpha: float = 0.05, power: float = 0.8) -> int:
    """per-arm n for a two-sided two-proportion test (fleiss, levin & paik 2003, ch. 4)."""
    if p1 == p2:
        raise ValueError("p1 and p2 must differ")
    z_alpha, z_beta = z_quantile(1 - alpha / 2), z_quantile(power)
    p_bar = (p1 + p2) / 2
    numerator = z_alpha * math.sqrt(2 * p_bar * (1 - p_bar)) + z_beta * math.sqrt(
        p1 * (1 - p1) + p2 * (1 - p2)
    )
    return math.ceil(numerator**2 / (p1 - p2) ** 2)


def one_sample_size(p0: float, p1: float, alpha: float = 0.05, power: float = 0.8) -> int:
    """n to detect p1 against a known rate p0, two-sided."""
    if p0 == p1:
        raise ValueError("p0 and p1 must differ")
    z_alpha, z_beta = z_quantile(1 - alpha / 2), z_quantile(power)
    numerator = z_alpha * math.sqrt(p0 * (1 - p0)) + z_beta * math.sqrt(p1 * (1 - p1))
    return math.ceil(numerator**2 / (p1 - p0) ** 2)


def minimum_detectable_lift(
    base_rate: float, n: int, alpha: float = 0.05, power: float = 0.8, *, two_sample: bool = True
) -> float | None:
    """smallest lift p2 / p1 detectable with n per arm; none when even p2 = 1 is out of reach."""
    if not 0 < base_rate < 1 or n <= 0:
        return None
    size = two_proportion_sample_size if two_sample else one_sample_size

    def needed(p2: float) -> int:
        return size(base_rate, p2, alpha, power)

    high = 1 - 1e-9
    if needed(high) > n:
        return None
    low = base_rate + 1e-9
    for _ in range(60):
        middle = (low + high) / 2
        if needed(middle) <= n:
            high = middle
        else:
            low = middle
    return high / base_rate
