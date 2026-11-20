import math
import statistics
from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta


def median(values: Iterable[float]) -> float | None:
    items = list(values)
    return statistics.median(items) if items else None


def clip01(value: float) -> float:
    return max(0.0, min(1.0, value))


def jaccard(left: Iterable[str], right: Iterable[str]) -> float:
    a, b = set(left), set(right)
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)


def weighted_quantile(values: Sequence[float], weights: Sequence[float], q: float) -> float:
    # interpolated on the cumulative weight midpoints, which matches numpy's default for equal weights
    pairs = sorted(zip(values, weights, strict=True))
    total = sum(weight for _, weight in pairs)
    if total <= 0:
        raise ValueError("weights must sum to a positive value")
    cumulative = 0.0
    points: list[tuple[float, float]] = []
    for value, weight in pairs:
        points.append(((cumulative + weight / 2) / total, value))
        cumulative += weight
    if q <= points[0][0]:
        return points[0][1]
    if q >= points[-1][0]:
        return points[-1][1]
    for (p_low, v_low), (p_high, v_high) in zip(points, points[1:], strict=False):
        if p_low <= q <= p_high:
            span = p_high - p_low
            return v_low if span == 0 else v_low + (v_high - v_low) * (q - p_low) / span
    return points[-1][1]


def effective_n(weights: Sequence[float]) -> float:
    squares = sum(weight * weight for weight in weights)
    return (sum(weights) ** 2) / squares if squares > 0 else 0.0


def floor_1dp(value: float) -> float:
    return math.floor(value * 10 + 1e-9) / 10


def ceil_1dp(value: float) -> float:
    return math.ceil(value * 10 - 1e-9) / 10


def max_share_within(times: Sequence[datetime], span: timedelta) -> float:
    # largest share of items published inside any window of the given span
    if not times:
        return 0.0
    ordered = sorted(times)
    best = 0
    start = 0
    for end, moment in enumerate(ordered):
        while moment - ordered[start] > span:
            start += 1
        best = max(best, end - start + 1)
    return best / len(ordered)
