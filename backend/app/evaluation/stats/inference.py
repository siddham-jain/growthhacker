from collections.abc import Sequence

import numpy as np


def holm(p_values: Sequence[float]) -> list[float]:
    """holm (1979) step-down adjusted p values, in the input order."""
    m = len(p_values)
    order = sorted(range(m), key=lambda i: p_values[i])
    adjusted = [0.0] * m
    running = 0.0
    for rank, index in enumerate(order):
        running = max(running, min(1.0, (m - rank) * p_values[index]))
        adjusted[index] = running
    return adjusted


def empirical_p(observed: float, null_samples: Sequence[float]) -> float:
    """share of null draws at least as large, never zero (phipson & smyth 2010)."""
    samples = np.asarray(null_samples, dtype=float)
    return float((1 + np.sum(samples >= observed)) / (samples.size + 1))


def percentile_of(observed: float, null_samples: Sequence[float]) -> float:
    samples = np.asarray(null_samples, dtype=float)
    if samples.size == 0:
        raise ValueError("percentile needs null samples")
    return float(np.mean(samples < observed) + 0.5 * np.mean(samples == observed))


def average_ranks(values: Sequence[float]) -> np.ndarray:
    array = np.asarray(values, dtype=float)
    order = np.argsort(array, kind="mergesort")
    ranks = np.empty(array.size)
    position = 0
    while position < array.size:
        end = position
        while end + 1 < array.size and array[order[end + 1]] == array[order[position]]:
            end += 1
        ranks[order[position : end + 1]] = (position + end) / 2 + 1
        position = end + 1
    return ranks


def spearman(x: Sequence[float], y: Sequence[float]) -> float | None:
    """pearson correlation of average ranks; none when either side is constant."""
    if len(x) != len(y) or len(x) < 3:
        return None
    rx, ry = average_ranks(x), average_ranks(y)
    if np.std(rx) == 0 or np.std(ry) == 0:
        return None
    return float(np.corrcoef(rx, ry)[0, 1])
