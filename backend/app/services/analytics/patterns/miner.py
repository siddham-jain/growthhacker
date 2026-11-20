from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from app.domain.analytics import AgeMethod, LiftScope, TraitLift
from app.services.analytics.patterns.contingency import Contingency, MiningUnit, split
from app.services.analytics.patterns.interactions import InteractionTest, interaction_tests
from app.services.analytics.patterns.stats import (
    bh_qvalues,
    fisher_one_sided,
    katz_rr_ci,
    quantiles,
    shrink,
    wilson,
)
from app.services.analytics.settings import PatternSettings


@dataclass(frozen=True, slots=True)
class Test:
    key: str
    value: str
    interaction: tuple[str, str] | None
    table: Contingency
    review_flagged: bool
    main_effect_rates: tuple[float, ...] = ()

    @property
    def label(self) -> str:
        base = f"{self.key}={self.value}"
        return base if self.interaction is None else f"{base}&{self.interaction[0]}={self.interaction[1]}"


@dataclass(frozen=True, slots=True)
class MiningRequest:
    scope: LiftScope
    window_days: int
    as_of: datetime
    comparison_age_days: int
    comparison_method: AgeMethod
    single_channel: bool = False


def main_effect_tests(units: Sequence[MiningUnit]) -> list[Test]:
    keys = sorted({key for u in units for key in u.features})
    tests = []
    for key in keys:
        population = [u for u in units if key in u.features]
        for value in sorted({v for u in population for v in u.features[key]}):
            table = split(population, lambda u, k=key, v=value: u.has(k, v))
            flagged = any((key, value) in u.review_flagged for u in table.with_units)
            tests.append(Test(key, value, None, table, flagged))
    return tests


class PatternMiner:
    """associations only, never causes: lift with smoothing, intervals, fdr and support gates."""

    def __init__(self, settings: PatternSettings) -> None:
        self.cfg = settings

    def mine(self, units: Sequence[MiningUnit], request: MiningRequest) -> list[TraitLift]:
        ordered = sorted(units, key=lambda u: (u.published_at, u.video_id))
        tests = main_effect_tests(ordered)
        if not request.single_channel:
            tests += [self._as_test(t) for t in interaction_tests(ordered, tests, self.cfg)]
        scope_rate = sum(u.is_outlier for u in ordered) / len(ordered) if ordered else 0.0
        testable = [t for t in tests if t.table.with_units and t.table.without_units]
        q_values = dict(
            zip(
                (t.label for t in testable),
                bh_qvalues([self._p_value(t.table) for t in testable]),
                strict=True,
            )
        )
        lifts = []
        for test in tests:
            lift = self._lift(test, request, scope_rate, q_values.get(test.label, 1.0))
            if lift is not None:
                lifts.append(lift)
        lifts = self._mark_redundant(lifts, {t.label: t for t in tests})
        return sorted(lifts, key=lambda x: (-x.lift_smoothed, x.trait_key, x.trait_value, str(x.interaction)))

    @staticmethod
    def _as_test(test: InteractionTest) -> Test:
        return Test(
            key=test.key,
            value=test.value,
            interaction=(test.other_key, test.other_value),
            table=test.table,
            review_flagged=test.review_flagged,
            main_effect_rates=test.main_effect_rates,
        )

    @staticmethod
    def _p_value(table: Contingency) -> float:
        return fisher_one_sided(table.a, table.b, table.c, table.d)

    def _lift(self, test: Test, request: MiningRequest, scope_rate: float, q: float) -> TraitLift | None:
        cfg, table = self.cfg, test.table
        a, b, c, d = table.a, table.b, table.c, table.d
        n_with, n_without = a + b, c + d
        p1 = a / n_with if n_with else 0.0
        p0 = c / n_without if n_without else 0.0
        smoothed = shrink(a, n_with, scope_rate, cfg.prior_strength_alpha) / max(
            shrink(c, n_without, scope_rate, cfg.prior_strength_alpha), 1e-12
        )
        balanced_with, balanced_without = table.balanced_rates()
        balanced = (
            balanced_with / balanced_without if balanced_with is not None and balanced_without else None
        )
        lift_ci = katz_rr_ci(a, b, c, d, cfg.z) if n_with and n_without else None
        balanced_ok = (
            balanced >= cfg.min_balanced_lift
            if balanced is not None
            else bool(balanced_with and balanced_without == 0)
        )
        status = self._status(test, request, lift_ci, q, balanced_ok, smoothed)
        if status is None:
            return None
        ordered_refs = sorted(table.outliers_with, key=lambda u: (-u.ratio, u.published_at, u.video_id))
        return TraitLift(
            scope=request.scope,
            window_days=request.window_days,
            as_of=request.as_of,
            comparison_age_days=request.comparison_age_days,
            comparison_method=request.comparison_method,
            trait_key=test.key,
            trait_value=test.value,
            interaction=test.interaction,
            n_with=n_with,
            n_without=n_without,
            outliers_with=a,
            outliers_without=c,
            outlier_rate_with=p1,
            outlier_rate_without=p0,
            rate_with_wilson=wilson(a, n_with, cfg.z),
            lift=p1 / p0 if a > 0 and c > 0 else None,
            lift_smoothed=smoothed,
            lift_ci=lift_ci,
            channel_balanced_lift=balanced,
            p_value=self._p_value(table),
            q_value=q,
            outlier_channels_with=table.outlier_channels_with(),
            max_channel_share=table.max_channel_share(),
            ratio_quantiles_with=quantiles([u.ratio for u in table.with_units], cfg.ratio_winsor_cap),
            reference_video_ids=[u.video_id for u in ordered_refs],
            status=status,
        )

    def _status(
        self,
        test: Test,
        request: MiningRequest,
        lift_ci: tuple[float, float] | None,
        q: float,
        balanced_ok: bool,
        smoothed: float,
    ) -> str | None:
        cfg, table = self.cfg, test.table
        n_with, n_without = len(table.with_units), len(table.without_units)
        if n_with < 1 or n_without < 1:
            return "no_contrast"
        min_outliers = cfg.channel_scope_min_outliers if request.single_channel else cfg.min_outliers_with
        if (
            table.effective_outliers_with() < min_outliers
            or (not request.single_channel and table.outlier_channels_with() < cfg.min_outlier_channels)
            or n_with < cfg.min_n_with
            or n_without < cfg.min_n_without
        ):
            return "insufficient_support"
        if not request.single_channel and table.max_channel_share() > cfg.max_channel_share:
            return "dominated"
        if self._supported(test, request, lift_ci, q, balanced_ok):
            return "supported"
        return "exploratory" if smoothed > 1 else None

    def _supported(
        self,
        test: Test,
        request: MiningRequest,
        lift_ci: tuple[float, float] | None,
        q: float,
        balanced_ok: bool,
    ) -> bool:
        # single-channel lifts never qualify; interactions must add over both main effects
        if request.single_channel or test.review_flagged or lift_ci is None:
            return False
        passes = lift_ci[0] > 1.0 and q <= self.cfg.fdr_q and balanced_ok
        if passes and test.interaction is not None:
            low = wilson(test.table.a, len(test.table.with_units), self.cfg.z)[0]
            passes = all(low > rate for rate in test.main_effect_rates)
        return passes

    def _mark_redundant(self, lifts: list[TraitLift], tests: dict[str, Test]) -> list[TraitLift]:
        def label(lift: TraitLift) -> str:
            base = f"{lift.trait_key}={lift.trait_value}"
            return base if lift.interaction is None else f"{base}&{lift.interaction[0]}={lift.interaction[1]}"

        supported = sorted(
            (x for x in lifts if x.status == "supported"),
            key=lambda x: (-x.lift_smoothed, x.trait_key, x.trait_value, str(x.interaction)),
        )
        kept: list[tuple[str, frozenset[str]]] = []
        redundant: dict[str, str] = {}
        for lift in supported:
            ids = frozenset(u.video_id for u in tests[label(lift)].table.with_units)
            twin = next(
                (
                    name
                    for name, other in kept
                    if len(ids & other) / len(ids | other) >= self.cfg.redundancy_jaccard
                ),
                None,
            )
            if twin is None:
                kept.append((label(lift), ids))
            else:
                redundant[label(lift)] = twin
        return [
            x.model_copy(update={"status": "redundant", "redundant_with": redundant[label(x)]})
            if label(x) in redundant
            else x
            for x in lifts
        ]
