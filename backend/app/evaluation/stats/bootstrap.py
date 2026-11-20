from collections import defaultdict
from collections.abc import Callable, Hashable, Mapping, Sequence
from dataclasses import dataclass

import numpy as np

# a statistic reads the resampled rows through their indices and may abstain (none) on a degenerate draw
Statistic = Callable[[np.ndarray], float | None]


@dataclass(frozen=True)
class BootstrapEstimate:
    estimate: float | None
    ci_low: float | None
    ci_high: float | None
    samples: np.ndarray
    resamples: int

    @property
    def ci(self) -> tuple[float, float] | None:
        if self.ci_low is None or self.ci_high is None:
            return None
        return self.ci_low, self.ci_high

    def share_at_or_below(self, value: float = 0.0) -> float | None:
        # one-sided bootstrap p for "estimate > value"
        if self.samples.size == 0:
            return None
        return float(np.mean(self.samples <= value))


def _cluster_index(strata: Sequence[Hashable], clusters: Sequence[Hashable]) -> list[list[np.ndarray]]:
    grouped: dict[Hashable, dict[Hashable, list[int]]] = defaultdict(lambda: defaultdict(list))
    for position, (stratum, cluster) in enumerate(zip(strata, clusters, strict=True)):
        grouped[stratum][cluster].append(position)
    return [
        [np.asarray(members, dtype=np.int64) for _, members in sorted(by_cluster.items(), key=_key)]
        for _, by_cluster in sorted(grouped.items(), key=_key)
    ]


def _key(item: tuple[Hashable, object]) -> str:
    return str(item[0])


def stratified_cluster_bootstrap(
    statistics: Mapping[str, Statistic],
    strata: Sequence[Hashable],
    clusters: Sequence[Hashable],
    resamples: int,
    rng: np.random.Generator,
    alpha: float = 0.05,
) -> dict[str, BootstrapEstimate]:
    """resample clusters with replacement inside each stratum; every statistic sees the same draw, so
    differences between arms are paired (davison & hinkley 1997 §3.8; field & welsh 2007)."""
    layout = _cluster_index(strata, clusters)
    everything = np.arange(len(strata), dtype=np.int64)
    point = {name: stat(everything) for name, stat in statistics.items()}
    draws: dict[str, list[float]] = {name: [] for name in statistics}
    for _ in range(resamples):
        parts = []
        for stratum in layout:
            picks = rng.integers(0, len(stratum), size=len(stratum))
            parts.extend(stratum[i] for i in picks)
        indices = np.concatenate(parts) if parts else everything[:0]
        for name, stat in statistics.items():
            value = stat(indices)
            if value is not None and np.isfinite(value):
                draws[name].append(float(value))
    estimates = {}
    for name in statistics:
        samples = np.asarray(draws[name], dtype=float)
        low = high = None
        if samples.size:
            low = float(np.quantile(samples, alpha / 2))
            high = float(np.quantile(samples, 1 - alpha / 2))
        estimates[name] = BootstrapEstimate(point[name], low, high, samples, resamples)
    return estimates


def item_bootstrap(
    statistics: Mapping[str, Statistic],
    n_items: int,
    resamples: int,
    rng: np.random.Generator,
    alpha: float = 0.05,
    strata: Sequence[Hashable] | None = None,
) -> dict[str, BootstrapEstimate]:
    """every item is its own cluster; optional strata keep the sampling design (stratified item bootstrap)."""
    return stratified_cluster_bootstrap(
        statistics, strata or [0] * n_items, list(range(n_items)), resamples, rng, alpha
    )
