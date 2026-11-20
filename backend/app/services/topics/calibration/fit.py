"""offline calibration from labelled pairs (used by the evaluation harness, never inside a run)."""

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import ArrayLike, NDArray


@dataclass(frozen=True)
class PairMetrics:
    precision: float
    recall: float
    f1: float
    true_positives: int
    false_positives: int
    false_negatives: int


def fit_logistic(
    features: ArrayLike, labels: ArrayLike, *, l2: float = 1e-3, iterations: int = 50
) -> NDArray[np.float64]:
    """newton-raphson logistic regression with a small ridge; column 0 should be the intercept."""
    x = np.asarray(features, dtype=np.float64)
    y = np.asarray(labels, dtype=np.float64)
    if x.ndim != 2 or x.shape[0] != y.shape[0] or not len(y):
        raise ValueError("features must be a non-empty (n, k) matrix aligned with labels")
    beta = np.zeros(x.shape[1])
    ridge = l2 * np.eye(x.shape[1])
    for _ in range(iterations):
        p = 1.0 / (1.0 + np.exp(-(x @ beta)))
        gradient = x.T @ (p - y) + ridge @ beta
        hessian = x.T @ (x * (p * (1 - p))[:, None]) + ridge
        step = np.linalg.solve(hessian, gradient)
        beta -= step
        if np.max(np.abs(step)) < 1e-8:
            break
    return beta


def fit_platt(margins: Sequence[float], labels: Sequence[int]) -> tuple[float, float]:
    """S = sigmoid(a * margin + b) fitted on a dev split."""
    x = np.column_stack([np.ones(len(margins)), np.asarray(margins, dtype=np.float64)])
    b, a = fit_logistic(x, labels)
    return float(a), float(b)


def combiner_features(
    lexical: float, semantic: float, title_hit: bool, tags_only: bool, quality_low: bool
) -> list[float]:
    return [
        1.0,
        lexical,
        semantic,
        lexical * semantic,
        float(title_hit),
        float(tags_only),
        float(quality_low),
    ]


def pick_thresholds(
    scores: Sequence[float],
    labels: Sequence[int],
    *,
    min_precision: float = 0.90,
    max_recall_loss: float = 0.03,
) -> tuple[float, float]:
    """accept = smallest score with precision >= target; reject = largest score losing <= the allowed recall."""
    order = np.argsort(scores)
    values = np.asarray(scores, dtype=np.float64)[order]
    truth = np.asarray(labels, dtype=np.int64)[order]
    positives = int(truth.sum())
    if not positives:
        raise ValueError("thresholds need at least one positive label")
    accept = 1.0
    for cut in np.unique(values):
        selected = truth[values >= cut]
        if selected.size and selected.mean() >= min_precision:
            accept = float(cut)
            break
    reject = 0.0
    for cut in np.unique(values):
        lost = int(truth[values < cut].sum())
        if lost / positives <= max_recall_loss:
            reject = float(cut)
    return accept, min(reject, accept)


def pair_metrics(predicted: set[tuple[str, str]], gold: set[tuple[str, str]]) -> PairMetrics:
    true_positives = len(predicted & gold)
    false_positives = len(predicted - gold)
    false_negatives = len(gold - predicted)
    precision = true_positives / len(predicted) if predicted else 0.0
    recall = true_positives / len(gold) if gold else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return PairMetrics(precision, recall, f1, true_positives, false_positives, false_negatives)


def expected_calibration_error(scores: Sequence[float], labels: Sequence[int], bins: int = 10) -> float:
    values = np.asarray(scores, dtype=np.float64)
    truth = np.asarray(labels, dtype=np.float64)
    if not len(values):
        return 0.0
    edges = np.linspace(0.0, 1.0, bins + 1)
    error = 0.0
    for low, high in zip(edges[:-1], edges[1:], strict=True):
        mask = (values >= low) & ((values < high) | (high == 1.0))
        if mask.any():
            error += mask.mean() * abs(values[mask].mean() - truth[mask].mean())
    return float(error)
