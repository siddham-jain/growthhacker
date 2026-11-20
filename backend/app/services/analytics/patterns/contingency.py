from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

from app.services.analytics.traits.base import Features


@dataclass(frozen=True, slots=True)
class MiningUnit:
    video_id: str
    channel_id: str
    is_outlier: bool
    ratio: float
    published_at: datetime
    features: Features
    review_flagged: frozenset[tuple[str, str]] = frozenset()
    cross_duplicate_group: str | None = None

    def has(self, key: str, value: str) -> bool:
        return value in self.features.get(key, frozenset())


@dataclass(frozen=True, slots=True)
class Contingency:
    with_units: tuple[MiningUnit, ...]
    without_units: tuple[MiningUnit, ...]

    @property
    def a(self) -> int:
        return sum(u.is_outlier for u in self.with_units)

    @property
    def b(self) -> int:
        return len(self.with_units) - self.a

    @property
    def c(self) -> int:
        return sum(u.is_outlier for u in self.without_units)

    @property
    def d(self) -> int:
        return len(self.without_units) - self.c

    @property
    def outliers_with(self) -> list[MiningUnit]:
        return [u for u in self.with_units if u.is_outlier]

    def effective_outliers_with(self) -> int:
        # cross-channel re-uploads count once, so no single re-upload can carry a pattern
        keys = {u.cross_duplicate_group or u.video_id for u in self.outliers_with}
        return len(keys)

    def outlier_channels_with(self) -> int:
        return len({u.channel_id for u in self.outliers_with})

    def max_channel_share(self) -> float:
        outliers = self.outliers_with
        if not outliers:
            return 0.0
        return max(Counter(u.channel_id for u in outliers).values()) / len(outliers)

    def balanced_rates(self) -> tuple[float | None, float | None]:
        # every channel gets equal total weight: each video weighs 1 / its channel's count in the population
        population = self.with_units + self.without_units
        per_channel = Counter(u.channel_id for u in population)

        def rate(units: Sequence[MiningUnit]) -> float | None:
            weight = sum(1 / per_channel[u.channel_id] for u in units)
            if weight == 0:
                return None
            return sum(1 / per_channel[u.channel_id] for u in units if u.is_outlier) / weight

        return rate(self.with_units), rate(self.without_units)


def split(units: Sequence[MiningUnit], has: Callable[[MiningUnit], bool]) -> Contingency:
    with_units = tuple(u for u in units if has(u))
    without_units = tuple(u for u in units if not has(u))
    return Contingency(with_units, without_units)
