from collections.abc import Iterable

from app.domain.analytics import LiftScope, TraitLift
from app.services.recommend.evidence.traits import PatternKind, trait_kind

PATTERN_SCOPES = (LiftScope.competitor_set, LiftScope.niche)
SAMPLE_MET_STATUSES = frozenset({"supported", "exploratory", "dominated", "redundant"})


def ci_low(lift: TraitLift) -> float | None:
    return lift.lift_ci[0] if lift.lift_ci else None


def ci_high(lift: TraitLift) -> float | None:
    return lift.lift_ci[1] if lift.lift_ci else None


def lift_traits(lift: TraitLift) -> dict[str, str]:
    traits = {lift.trait_key: lift.trait_value}
    if lift.interaction is not None:
        traits[lift.interaction[0]] = lift.interaction[1]
    return traits


def is_lifted(lift: TraitLift, min_outliers: int) -> bool:
    low = ci_low(lift)
    return lift.status == "supported" and low is not None and low > 1.0 and lift.outliers_with >= min_outliers


def sample_met(lift: TraitLift) -> bool:
    return lift.status in SAMPLE_MET_STATUSES


class LiftBook:
    """06's trait lifts for one window, looked up by scope and trait."""

    def __init__(self, lifts: Iterable[TraitLift]) -> None:
        self._lifts: dict[tuple[LiftScope, str, str, tuple[str, str] | None], TraitLift] = {}
        for lift in lifts:
            self._lifts[(lift.scope, lift.trait_key, lift.trait_value, lift.interaction)] = lift

    def get(
        self, scope: LiftScope, key: str, value: str, interaction: tuple[str, str] | None = None
    ) -> TraitLift | None:
        return self._lifts.get((scope, key, value, interaction))

    def best_supported(self, key: str, value: str, min_outliers: int) -> TraitLift | None:
        for scope in PATTERN_SCOPES:
            lift = self.get(scope, key, value)
            if lift is not None and is_lifted(lift, min_outliers):
                return lift
        return None

    def ci_low(self, scope: LiftScope, key: str, value: str) -> float:
        lift = self.get(scope, key, value)
        return (ci_low(lift) or 0.0) if lift else 0.0

    def pattern_candidates(self, kind: PatternKind, min_outliers: int) -> list[TraitLift]:
        # competitor scope wins when both scopes support the same trait (closer to the client)
        chosen: dict[tuple[str, str, tuple[str, str] | None], TraitLift] = {}
        for (scope, key, value, interaction), lift in sorted(self._lifts.items(), key=_order):
            if scope not in PATTERN_SCOPES or not is_lifted(lift, min_outliers):
                continue
            if trait_kind(key) != kind or (interaction and trait_kind(interaction[0]) != kind):
                continue
            chosen.setdefault((key, value, interaction), lift)
        return list(chosen.values())

    def opposite(self, lift: TraitLift) -> TraitLift | None:
        other = LiftScope.niche if lift.scope is LiftScope.competitor_set else LiftScope.competitor_set
        return self.get(other, lift.trait_key, lift.trait_value, lift.interaction)


def _order(item: tuple[tuple[LiftScope, str, str, tuple[str, str] | None], TraitLift]) -> tuple:
    (scope, key, value, interaction), _ = item
    return (
        PATTERN_SCOPES.index(scope) if scope in PATTERN_SCOPES else 9,
        key,
        value,
        interaction or ("", ""),
    )
