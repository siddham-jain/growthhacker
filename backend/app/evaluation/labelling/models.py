from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

LabelValue = Literal["outlier", "not_outlier", "unsure"]
GoldMethod = Literal["agree", "adjudicated", "single", "excluded", "pending"]


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class UploadPoint(_Model):
    video_id: str
    title: str
    published_at: datetime
    views: int
    age_days: float
    segment: str


class PopulationVideo(_Model):
    video_id: str
    channel_id: str
    channel_title: str
    client_ids: tuple[str, ...]
    title: str
    segment: str
    duration_s: int | None
    published_at: datetime
    age_days: float
    views: int
    ratio: float
    is_outlier: bool
    subscriber_count: int | None
    hidden_subscribers: bool
    thumbnail_url: str | None
    oldest_api_at: datetime


class Population(_Model):
    observed_at: datetime
    threshold: float
    detector_version: str
    videos: tuple[PopulationVideo, ...]
    uploads: dict[str, tuple[UploadPoint, ...]]
    excluded_no_ratio: int = 0


class SampledTask(_Model):
    task_id: UUID
    batch_id: UUID
    video: PopulationVideo
    stratum: str
    inclusion_prob: float


class SampleDesign(_Model):
    batch_id: UUID
    tasks: tuple[SampledTask, ...]
    cell_population: dict[str, int]
    cell_sampled: dict[str, int]
    shortfall: dict[str, int]
    notes: tuple[str, ...] = ()


class TaskRecord(_Model):
    # the stored task as the scorer sees it; detector fields are never shown to labellers
    task_id: UUID
    batch_id: UUID
    video_id: str | None
    channel_id: str | None
    stratum: str
    inclusion_prob: float
    detector_ratio: float | None
    detector_label: bool
    detector_version: str
    observed_at: datetime


class LabelRecord(_Model):
    task_id: UUID
    labeller_id: UUID
    round: int
    label: LabelValue
    reason_codes: tuple[str, ...] = ()
    note: str | None = None
    seconds_spent: int | None = None
    labelled_at: datetime
    # set when a workspace labels through the api; offline label packs carry none
    workspace_id: UUID | None = None


class GoldLabel(_Model):
    task_id: UUID
    gold: LabelValue | None
    method: GoldMethod


class LabelAgreement(_Model):
    batch_id: UUID
    n_items: int
    percent_agreement: float | None
    cohen_kappa: float | None
    gwet_ac1: float | None
    n_unsure: dict[str, int]
    labellers: tuple[str, ...]


class ThresholdPoint(_Model):
    threshold: float
    precision_w: float | None
    recall_w: float | None
    f05_w: float | None
    f1_w: float | None
    precision_ci: tuple[float, float] | None
    recall_ci: tuple[float, float] | None


class OutlierEvalResult(_Model):
    batch_id: UUID
    detector_version: str
    threshold: float
    n_tasks: int
    n_gold: int
    gold_methods: dict[str, int]
    excluded_share: float
    agreement: LabelAgreement
    confusion_weighted: dict[str, float]
    confusion_raw: dict[str, int]
    precision_w: float | None
    recall_w: float | None
    f1_w: float | None
    f05_w: float | None
    precision_ci: tuple[float, float] | None
    recall_ci: tuple[float, float] | None
    precision_wilson_raw: tuple[float, float] | None
    recall_wilson_raw: tuple[float, float] | None
    by_stratum: dict[str, dict[str, float | int | None]]
    by_client: dict[str, dict[str, float | int | None]]
    reason_codes: dict[str, dict[str, int]]
    curve: tuple[ThresholdPoint, ...]
    recommended_threshold: float | None
    loco_optimism_f05: float | None
    warnings: tuple[str, ...]
    variants: dict[str, dict[str, float]] = {}
