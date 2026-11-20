import hashlib
import json
import math
from datetime import date

from pydantic import BaseModel, ConfigDict

from app.core.config import Settings


class _Group(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class AgeSettings(_Group):
    target_ages_days: tuple[int, ...] = (1, 3, 7, 30)
    comparison_age_by_window: dict[int, int] = {7: 3, 30: 7, 90: 7}
    secondary_age_by_window: dict[int, int] = {90: 30}
    tol_hours: dict[int, float] = {1: 3, 3: 6, 7: 12, 30: 48}
    max_gap_hours: dict[int, float] = {1: 12, 3: 36, 7: 72, 30: 240}
    max_bracket_spread: float = 0.25
    snapshot_dedupe_minutes: float = 5
    freeze_hours: float = 48
    freeze_max_age_days: float = 7

    def tol_days(self, age: int) -> float:
        return self.tol_hours[age] / 24

    def max_gap_days_for(self, age_days: float) -> float:
        # peers at arbitrary ages reuse the gap of the nearest target age at or above them
        for target in sorted(self.max_gap_hours):
            if age_days <= target:
                return self.max_gap_hours[target] / 24
        return self.max_gap_hours[max(self.max_gap_hours)] / 24


class ProxySettings(_Group):
    kappa_by_age: tuple[tuple[float, float], ...] = ((3, 1.25), (30, 1.5), (180, 2.0), (math.inf, 3.0))
    widen_factor: float = 1.33
    proxy_max_peers: int = 30
    proxy_max_widenings: int = 2
    true_mode_min_coverage: float = 0.80

    def kappa(self, age_days: float) -> float:
        for upper, kappa in self.kappa_by_age:
            if age_days < upper:
                return kappa
        return self.kappa_by_age[-1][1]


class SegmentSettings(_Group):
    dup_title_ratio: float = 95
    dup_duration_tol_s: int = 2
    dup_window_days: int = 30
    shorts_regime_cutoff: date = date(2025, 3, 31)


class BaselineSettings(_Group):
    lookback_videos: int = 30
    lookback_days: int = 365
    widened_lookback_videos: int = 50
    widened_lookback_days: int = 730
    min_baseline_n: int = 5
    min_baseline_views: float = 10
    mad_floor: float = 0.05
    baseline_split_by_language: bool = True
    language_split_min_ratio: float = 2.0


class OutlierSettings(_Group):
    outlier_ratio: float = 3.0
    strong_ratio: float = 5.0
    breakout_ratio: float = 2.0
    below_ratio: float = 0.67
    outlier_min_z: float = 2.0
    min_outlier_views: int = 100
    engagement_spike_divisor: float = 5
    min_baseline_views_for_outliers: float = 100


class RateSettings(_Group):
    min_views_for_rates: int = 100
    min_velocity_interval_h: float = 6
    recent_window_h: tuple[float, float] = (20, 30)
    velocity_7d_min_days: float = 7
    velocity_ratio_min_peers: int = 5


class TraitSettings(_Group):
    topic_min_score: float = 0.5
    weekend_days: tuple[int, ...] = (5, 6)


class ThumbnailSettings(_Group):
    source_order: tuple[str, ...] = ("maxres", "standard", "high", "medium")
    max_image_bytes: int = 2_000_000
    letterbox_luma: float = 0.06
    letterbox_std: float = 0.02
    letterbox_max_share: float = 0.25
    pillarbox_std: float = 0.03
    pillarbox_min_share: float = 0.18
    stats_max_width: int = 320
    ocr_min_height: int = 720
    ocr_min_conf: float = 0.6
    ocr_min_box_height_ratio: float = 0.04
    ocr_unreadable_conf: float = 0.5
    recurring_text_share: float = 0.40
    recurring_lookback_thumbnails: int = 30
    recurring_min_thumbnails: int = 5
    devanagari_title_share: float = 0.10
    brightness_bands: tuple[float, float] = (0.35, 0.65)
    contrast_bands: tuple[float, float] = (0.18, 0.28)
    colorfulness_bands: tuple[float, float] = (33, 59)
    dominant_colour_min_share: float = 0.05
    degenerate_brightness: float = 0.05
    degenerate_contrast: float = 0.02


class CommentSettings(_Group):
    max_comments_per_video: int = 100
    min_comments_for_signals: int = 10
    comment_excerpts_enabled: bool = False
    regulated_niches: tuple[str, ...] = ("healthcare", "finance")
    max_excerpts: int = 3
    excerpt_max_chars: int = 140
    spam_repeat_videos: int = 3
    min_letters: int = 3
    buyer_question_similarity: float = 0.85


class PatternSettings(_Group):
    min_outliers_with: int = 3
    min_outlier_channels: int = 2
    min_n_with: int = 8
    min_n_without: int = 8
    max_channel_share: float = 0.5
    fdr_q: float = 0.10
    prior_strength_alpha: float = 5
    min_balanced_lift: float = 1.2
    redundancy_jaccard: float = 0.8
    ratio_winsor_cap: float = 50
    interaction_pairs: tuple[tuple[str, str], ...] = (
        ("topic", "segment"),
        ("topic", "length_band"),
        ("topic", "title.is_question"),
        ("segment", "title.is_how_to"),
    )
    interaction_min_n: int = 40
    channel_scope_min_outliers: int = 2
    z: float = 1.959964


class ListSettings(_Group):
    max_per_channel_in_lists: int = 2
    trending_min_views: int = 500
    trending_top_n: int = 20
    best_worst_n: int = 3
    min_group_n: int = 3
    rising_min_outliers: int = 2
    rising_factor: float = 2.0


class ReportSettings(_Group):
    gap_lookback_days: int = 365
    gap_min_competitors: int = 2
    cadence_lookback_days: int = 90
    min_uploads_for_gap_cv: int = 4
    top_search_terms: int = 10


class AnalyticsEngineSettings(_Group):
    ages: AgeSettings = AgeSettings()
    proxy: ProxySettings = ProxySettings()
    segments: SegmentSettings = SegmentSettings()
    baselines: BaselineSettings = BaselineSettings()
    outliers: OutlierSettings = OutlierSettings()
    rates: RateSettings = RateSettings()
    traits: TraitSettings = TraitSettings()
    thumbnails: ThumbnailSettings = ThumbnailSettings()
    comments: CommentSettings = CommentSettings()
    patterns: PatternSettings = PatternSettings()
    lists: ListSettings = ListSettings()
    reports: ReportSettings = ReportSettings()
    windows: tuple[int, ...] = (7, 30, 90)
    retention_mode: str = "strict_30d"

    @classmethod
    def from_settings(cls, settings: Settings) -> "AnalyticsEngineSettings":
        # the core settings tree stays the single override point for the shared thresholds
        core = settings.analytics
        base = cls()
        return base.model_copy(
            update={
                "windows": tuple(core.windows),
                "retention_mode": settings.policy.retention_mode,
                "ages": base.ages.model_copy(
                    update={"comparison_age_by_window": dict(core.comparison_age_days)}
                ),
                "proxy": base.proxy.model_copy(update={"true_mode_min_coverage": core.proxy_switch_coverage}),
                "outliers": base.outliers.model_copy(
                    update={
                        "outlier_ratio": core.outlier_ratio_min,
                        "min_outlier_views": core.outlier_view_floor,
                    }
                ),
                "patterns": base.patterns.model_copy(
                    update={
                        "min_outliers_with": core.lift_min_outliers,
                        "min_outlier_channels": core.lift_min_channels,
                        "fdr_q": core.lift_fdr_q,
                    }
                ),
            }
        )

    def comparison_age(self, window_days: int) -> int:
        return self.ages.comparison_age_by_window[window_days]

    def comparison_ages(self) -> tuple[int, ...]:
        ages = {self.comparison_age(window) for window in self.windows}
        ages |= {
            self.ages.secondary_age_by_window[w]
            for w in self.windows
            if w in self.ages.secondary_age_by_window
        }
        return tuple(sorted(ages))

    def settings_hash(self) -> str:
        payload = json.dumps(self.model_dump(mode="json"), sort_keys=True, default=str)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]
