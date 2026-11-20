import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from app.core.registry import Registry
from app.domain.analytics import ChannelBaseline, OutlierAssessment, OutlierTier
from app.services.analytics.baselines import AgeValue, VideoFacts
from app.services.analytics.settings import AnalyticsEngineSettings, OutlierSettings

OUTLIER_TIERS = frozenset({OutlierTier.outlier, OutlierTier.strong_outlier})


class OutlierRule(Protocol):
    def assess(self, value: int, baseline: ChannelBaseline) -> tuple[OutlierTier, float, float]: ...


class DualGateRule:
    """ratio against the channel's own median plus a log-space robust z gate and an absolute floor."""

    def __init__(self, outliers: OutlierSettings, min_baseline_views: float, mad_floor: float) -> None:
        self.cfg = outliers
        self.min_baseline_views = min_baseline_views
        self.mad_floor = mad_floor

    def assess(self, value: int, baseline: ChannelBaseline) -> tuple[OutlierTier, float, float]:
        effective = max(baseline.median, self.min_baseline_views)
        ratio = value / effective
        z = (math.log10(max(value, 1)) - math.log10(max(baseline.median, 1.0))) / max(
            baseline.mad_log10, self.mad_floor
        )
        if not baseline.sufficient:
            return OutlierTier.insufficient, ratio, z
        far_enough = z >= self.cfg.outlier_min_z and value >= self.cfg.min_outlier_views
        if ratio >= self.cfg.strong_ratio and far_enough:
            return OutlierTier.strong_outlier, ratio, z
        if ratio >= self.cfg.outlier_ratio and far_enough:
            return OutlierTier.outlier, ratio, z
        if ratio >= self.cfg.breakout_ratio:
            return OutlierTier.breakout, ratio, z
        if ratio >= self.cfg.below_ratio:
            return OutlierTier.normal, ratio, z
        return OutlierTier.below, ratio, z


@dataclass(frozen=True, slots=True)
class SuppressionInput:
    fact: VideoFacts
    tier: OutlierTier
    baseline: ChannelBaseline
    channel_like_rate: ChannelBaseline | None
    duplicate_secondary: bool


class SuppressionRule(Protocol):
    reason: str

    def applies(self, item: SuppressionInput, settings: AnalyticsEngineSettings) -> bool: ...


suppression_rule_registry: Registry[type[SuppressionRule]] = Registry("suppression_rule_registry")


@suppression_rule_registry.register("duplicate")
class DuplicateSuppression:
    reason = "duplicate"

    def applies(self, item: SuppressionInput, settings: AnalyticsEngineSettings) -> bool:
        return item.duplicate_secondary


@suppression_rule_registry.register("views_without_engagement")
class ViewsWithoutEngagement:
    # views far above normal while likes sit far below the channel's norm; never asserted to be ads
    reason = "views_without_engagement"

    def applies(self, item: SuppressionInput, settings: AnalyticsEngineSettings) -> bool:
        norm = item.channel_like_rate
        if (
            item.tier not in OUTLIER_TIERS
            or item.fact.like_rate is None
            or norm is None
            or not norm.sufficient
        ):
            return False
        return item.fact.like_rate <= norm.median / settings.outliers.engagement_spike_divisor


@suppression_rule_registry.register("tiny_baseline")
class TinyBaseline:
    reason = "tiny_baseline"

    def applies(self, item: SuppressionInput, settings: AnalyticsEngineSettings) -> bool:
        floor = settings.outliers.min_baseline_views_for_outliers
        return item.tier in OUTLIER_TIERS and item.baseline.median < floor


DEFAULT_SUPPRESSIONS: tuple[str, ...] = ("duplicate", "views_without_engagement", "tiny_baseline")


class OutlierAssessor:
    def __init__(
        self, settings: AnalyticsEngineSettings, suppressions: Sequence[str] = DEFAULT_SUPPRESSIONS
    ) -> None:
        self.settings = settings
        self.rule: OutlierRule = DualGateRule(
            settings.outliers, settings.baselines.min_baseline_views, settings.baselines.mad_floor
        )
        self.suppressions = [suppression_rule_registry.get(key)() for key in suppressions]

    def assess(
        self,
        fact: VideoFacts,
        age: int,
        value: AgeValue,
        baseline: ChannelBaseline,
        channel_like_rate: ChannelBaseline | None,
        duplicate_secondary: bool,
    ) -> OutlierAssessment:
        tier, ratio, z = self.rule.assess(value.value, baseline)
        item = SuppressionInput(fact, tier, baseline, channel_like_rate, duplicate_secondary)
        reason = next((rule.reason for rule in self.suppressions if rule.applies(item, self.settings)), None)
        return OutlierAssessment(
            age_days=age,
            method=value.method,
            value=value.value,
            baseline_median=baseline.median,
            baseline_n=baseline.n,
            baseline_id=baseline.baseline_id,
            ratio=ratio,
            log_robust_z=z,
            tier=tier,
            is_outlier=tier in OUTLIER_TIERS and reason is None,
            suppressed_reason=reason,
        )
