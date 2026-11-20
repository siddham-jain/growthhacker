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
