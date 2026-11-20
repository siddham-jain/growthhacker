from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.services.analytics.patterns.contingency import Contingency, MiningUnit, split
from app.services.analytics.settings import PatternSettings


@dataclass(frozen=True, slots=True)
class InteractionTest:
    key: str
    value: str
    other_key: str
    other_value: str
    table: Contingency
    review_flagged: bool
    main_effect_rates: tuple[float, float]


def interaction_tests(
    units: Sequence[MiningUnit], main_tests: Sequence[Any], settings: PatternSettings
) -> list[InteractionTest]:
    # pre-registered two-way pairs only, which caps the number of tests; no three-way interactions
    mains = {(t.key, t.value): t.table for t in main_tests}
    tests: list[InteractionTest] = []
    for key, other_key in settings.interaction_pairs:
        population = [u for u in units if key in u.features and other_key in u.features]
        if len(population) < settings.interaction_min_n:
            continue
        values = sorted({v for u in population for v in u.features[key]})
        others = sorted({v for u in population for v in u.features[other_key]})
        for value in values:
            for other in others:
                first, second = mains.get((key, value)), mains.get((other_key, other))
                if first is None or second is None:
                    continue
                if (
                    len(first.with_units) < settings.min_n_with
                    or len(second.with_units) < settings.min_n_with
                ):
                    continue
                table = split(
                    population, lambda u, k=key, a=value, o=other_key, b=other: u.has(k, a) and u.has(o, b)
                )
                if (
                    table.a < settings.min_outliers_with
                    or table.outlier_channels_with() < settings.min_outlier_channels
                ):
                    continue
                flagged = any(
                    (key, value) in u.review_flagged or (other_key, other) in u.review_flagged
                    for u in table.with_units
                )
                rates = (first.a / len(first.with_units), second.a / len(second.with_units))
                tests.append(InteractionTest(key, value, other_key, other, table, flagged, rates))
    return tests
