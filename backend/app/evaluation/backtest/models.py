import hashlib
import json
from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.domain.recommendations import MatchSpec
from app.pipeline.contracts import RunConfigSnapshot

Arm = Literal["system", "copy_top", "random"]
ARMS: tuple[Arm, ...] = ("system", "copy_top", "random")
OutcomeStatus = Literal["complete", "pending", "missing_snapshot", "deleted", "no_baseline"]
Verdict = Literal["supported", "not_supported", "inconclusive_underpowered", "invalid"]


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class BacktestConfig(_Model):
    """pre-registered before any outcome is read; the hash is checked again at scoring (edge case 15)."""

    mode: Literal["forward"] = "forward"
    client_id: UUID
    workspace_id: UUID
    freeze_id: UUID
    run_id: UUID
    as_of: AwareDatetime
    window_days: Literal[7, 30, 90]
    horizon_days: int
    outcome_age_days: int
    k_per_type: dict[str, int]
    random_replicates: int
    bootstrap_resamples: int
    alpha: float
    seed: int
    outlier_threshold: float
    include_own_channel: bool
    floors: dict[str, int]
    max_missing_share: float
    max_usd: float
    max_quota_units: int
    code_version: str
    # scoring inputs frozen at T and registered before any outcome is read
    snapshot: RunConfigSnapshot
    universe: list[str]
    own_channel_id: str | None
    frozen_baselines: dict[str, dict[str, float | str]]
    leakage_canary_passed: bool
    pit_level: str
    late_tables: list[str] = []

    def canonical_hash(self) -> str:
        payload = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()

    def outcomes_mature_at(self) -> datetime:
        return self.as_of + timedelta(days=self.horizon_days + self.outcome_age_days + 1)


class ArmSpec(_Model):
    arm: Arm
    spec: MatchSpec
    replicate: int | None = None
    confidence: str | None = None
    expected_low: float | None = None
    expected_high: float | None = None

    @property
    def key(self) -> str:
        return self.spec.spec_id


class CorpusVideo(_Model):
    """one video as the matcher and the arms see it; traits flattened exactly as 06 mines them."""

    video_id: str
    channel_id: str
    published_at: AwareDatetime
    segment: str
    traits: dict[str, str]
    topic_ids: frozenset[str] = frozenset()
    topic_scores: dict[str, float] = {}
    views_at_t: int | None = None
    is_own: bool = False


class OutcomeVideo(_Model):
    video_id: str
    channel_id: str
    client_id: UUID
    published_at: AwareDatetime
    segment: str
    views_at_age: int | None
    views_at_age_method: str
    baseline_at_t: float | None
    baseline_method: str | None
    ratio: float | None
    is_outlier: bool | None
    detector_ratio: float | None = None
    detector_is_outlier: bool | None = None
    status: OutcomeStatus
    traits: dict[str, str] = {}
    topic_ids: frozenset[str] = frozenset()

    def as_corpus(self) -> CorpusVideo:
        return CorpusVideo(
            video_id=self.video_id,
            channel_id=self.channel_id,
            published_at=self.published_at,
            segment=self.segment,
            traits=self.traits,
            topic_ids=self.topic_ids,
        )


class ArmMetrics(_Model):
    arm: str
    scope: str
    n_specs: int
    n_specs_matched: int
    n_specs_untestable: int
    n_videos_matched: int
    n_channels_matched: int
    coverage: float | None
    outlier_rate: float | None
    outlier_rate_ci: tuple[float, float] | None
    outlier_rate_wilson: tuple[float, float] | None
    geo_mean_ratio: float | None
    geo_mean_ratio_ci: tuple[float, float] | None
    spec_hit_rate: float | None
    lift_vs_base: float | None
    lift_vs_base_ci: tuple[float, float] | None
    few_clusters: bool = False


class ArmComparison(_Model):
    scope: str
    a: str
    b: str
    metric: Literal["outlier_rate_diff", "risk_ratio", "log_ratio_diff", "outlier_rate_vs_random"]
    estimate: float | None
    ci: tuple[float, float] | None
    p_value: float | None
    p_adjusted: float | None = None
    method: str
    primary: bool = False
    note: str | None = None


class BacktestReport(_Model):
    eval_ids: list[UUID]
    configs: list[BacktestConfig]
    freezes: list[dict[str, Any]]
    verdict: Verdict
    headline: str
    arms: list[ArmMetrics]
    comparisons: list[ArmComparison]
    random_percentile: float | None
    mde: dict[str, float | None]
    calibration: dict[str, Any]
    lens_split: dict[str, dict[str, float | int | None]]
    within_channel: dict[str, float | int | None]
    overlap: dict[str, float | bool | None]
    missingness: dict[str, int]
    abstentions: list[str]
    sensitivity: dict[str, float | int | None]
    limitations: list[str]
    spec_table: list[dict[str, Any]] = Field(default_factory=list)
    generated_at: AwareDatetime
