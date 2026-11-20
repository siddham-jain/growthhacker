from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class WeightedConfusion:
    tp: float
    fp: float
    fn: float
    tn: float

    @property
    def precision(self) -> float | None:
        flagged = self.tp + self.fp
        return self.tp / flagged if flagged else None

    @property
    def recall(self) -> float | None:
        positives = self.tp + self.fn
        return self.tp / positives if positives else None

    def f_beta(self, beta: float) -> float | None:
        return f_beta(self.precision, self.recall, beta)


def weighted_confusion(
    gold: Sequence[bool], predicted: Sequence[bool], weights: Sequence[float] | None = None
) -> WeightedConfusion:
    """horvitz-thompson weighted counts (horvitz & thompson 1952); unit weights give the raw matrix."""
    if len(gold) != len(predicted) or (weights is not None and len(weights) != len(gold)):
        raise ValueError("gold, predicted and weights must align")
    tp = fp = fn = tn = 0.0
    for position, (truth, flagged) in enumerate(zip(gold, predicted, strict=True)):
        weight = 1.0 if weights is None else float(weights[position])
        if truth and flagged:
            tp += weight
        elif flagged:
            fp += weight
        elif truth:
            fn += weight
        else:
            tn += weight
    return WeightedConfusion(tp, fp, fn, tn)


def weighted_precision_recall(
    gold: Sequence[bool], predicted: Sequence[bool], weights: Sequence[float]
) -> tuple[float | None, float | None]:
    confusion = weighted_confusion(gold, predicted, weights)
    return confusion.precision, confusion.recall


def f_beta(precision: float | None, recall: float | None, beta: float) -> float | None:
    if precision is None or recall is None:
        return None
    if precision == 0 and recall == 0:
        return 0.0
    b2 = beta * beta
    return (1 + b2) * precision * recall / (b2 * precision + recall)


def inclusion_probability(sampled: int, population: int) -> float:
    if population <= 0 or not 0 <= sampled <= population:
        raise ValueError("a cell needs 0 <= sampled <= population and a non-empty population")
    return sampled / population
