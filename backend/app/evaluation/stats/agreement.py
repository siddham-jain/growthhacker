from collections import Counter
from collections.abc import Hashable, Sequence
from itertools import permutations
from typing import Literal

import numpy as np

AlphaLevel = Literal["nominal", "ordinal", "interval"]


def _paired(a: Sequence[Hashable], b: Sequence[Hashable]) -> None:
    if len(a) != len(b):
        raise ValueError("both raters must label the same items")
    if not a:
        raise ValueError("agreement needs at least one item")


def percent_agreement(a: Sequence[Hashable], b: Sequence[Hashable]) -> float:
    _paired(a, b)
    return sum(x == y for x, y in zip(a, b, strict=True)) / len(a)


def cohen_kappa(a: Sequence[Hashable], b: Sequence[Hashable]) -> float:
    """cohen (1960); 1.0 when chance agreement is already perfect."""
    _paired(a, b)
    n = len(a)
    observed = percent_agreement(a, b)
    left, right = Counter(a), Counter(b)
    expected = sum(left[c] * right[c] for c in set(left) | set(right)) / (n * n)
    if expected == 1:
        return 1.0
    return (observed - expected) / (1 - expected)


def gwet_ac1(a: Sequence[Hashable], b: Sequence[Hashable]) -> float:
    """gwet (2008) first-order agreement coefficient; stable under skewed prevalence."""
    _paired(a, b)
    n = len(a)
    categories = sorted(set(a) | set(b), key=str)
    if len(categories) < 2:
        return 1.0
    observed = percent_agreement(a, b)
    left, right = Counter(a), Counter(b)
    shares = [(left[c] + right[c]) / (2 * n) for c in categories]
    expected = sum(p * (1 - p) for p in shares) / (len(categories) - 1)
    if expected == 1:
        return 1.0
    return (observed - expected) / (1 - expected)


def weighted_kappa(
    a: Sequence[int], b: Sequence[int], categories: Sequence[int], weights: Literal["linear", "quadratic"]
) -> float:
    """cohen (1968) weighted kappa over ordered categories."""
    _paired(a, b)
    index = {c: i for i, c in enumerate(categories)}
    k = len(categories)
    observed = np.zeros((k, k))
    for x, y in zip(a, b, strict=True):
        observed[index[x], index[y]] += 1
    observed /= len(a)
    expected = np.outer(observed.sum(axis=1), observed.sum(axis=0))
    i, j = np.indices((k, k))
    distance = np.abs(i - j) / max(k - 1, 1)
    disagreement = distance if weights == "linear" else distance**2
    expected_disagreement = float((disagreement * expected).sum())
    if expected_disagreement == 0:
        return 1.0
    return 1 - float((disagreement * observed).sum()) / expected_disagreement


def krippendorff_alpha(units: Sequence[Sequence[float | None]], level: AlphaLevel) -> float | None:
    """krippendorff (2011); units are rows of one value per coder, none for missing. none when undefined."""
    pairable = [[v for v in unit if v is not None] for unit in units]
    pairable = [values for values in pairable if len(values) >= 2]
    if not pairable:
        return None
    values = sorted({v for unit in pairable for v in unit})
    if len(values) < 2:
        return None
    index = {v: i for i, v in enumerate(values)}
    k = len(values)
    coincidence = np.zeros((k, k))
    for unit in pairable:
        m = len(unit)
        for x, y in permutations(range(m), 2):
            coincidence[index[unit[x]], index[unit[y]]] += 1 / (m - 1)
    marginals = coincidence.sum(axis=1)
    total = marginals.sum()
    delta = _metric(values, marginals, level)
    observed = float((coincidence * delta).sum())
    expected = float((np.outer(marginals, marginals) * delta).sum()) / (total - 1)
    if expected == 0:
        return None
    return 1 - observed / expected


def _metric(values: list[float], marginals: np.ndarray, level: AlphaLevel) -> np.ndarray:
    k = len(values)
    if level == "nominal":
        return 1 - np.eye(k)
    if level == "interval":
        array = np.asarray(values, dtype=float)
        return (array[:, None] - array[None, :]) ** 2
    delta = np.zeros((k, k))
    for g in range(k):
        for h in range(k):
            low, high = min(g, h), max(g, h)
            between = marginals[low : high + 1].sum() - (marginals[g] + marginals[h]) / 2
            delta[g, h] = between**2
    return delta
