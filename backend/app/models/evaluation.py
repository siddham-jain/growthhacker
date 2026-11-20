import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    REAL,
    BigInteger,
    CheckConstraint,
    Double,
    ForeignKey,
    Identity,
    Index,
    SmallInteger,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    Base,
    Zone,
    check_in,
    created_at_column,
    jsonb_array,
    jsonb_object,
    schema_version_column,
    text_array,
    updated_at_column,
    uuid_pk,
)
from app.models.youtube_derived import AGE_METHODS

REVIEW_CRITERIA = ("specific", "evidenced", "relevant", "actionable", "not_obvious")


class EvalConfig(Base):
    __tablename__ = "eval_config"
    __zone__ = Zone.evaluation

    eval_id: Mapped[uuid.UUID] = uuid_pk()
    mode: Mapped[str]
    config: Mapped[dict[str, Any]] = jsonb_object()
    config_schema_version: Mapped[int] = schema_version_column()
    config_hash: Mapped[str]
    code_version: Mapped[str]
    seed: Mapped[int] = mapped_column(BigInteger)
    registered_at: Mapped[datetime] = created_at_column()
    status: Mapped[str] = mapped_column(index=True, server_default=text("'registered'"))
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    usd_spent: Mapped[Decimal] = mapped_column(server_default=text("0"))
    units_spent: Mapped[int] = mapped_column(server_default=text("0"))


class EvalFreeze(Base):
    __tablename__ = "eval_freeze"
    __zone__ = Zone.evaluation
    __table_args__ = (Index("ix_eval_freeze_client_id_as_of", "client_id", "as_of"),)

    freeze_id: Mapped[uuid.UUID] = uuid_pk()
    workspace_id: Mapped[uuid.UUID | None]
    client_id: Mapped[uuid.UUID | None]
    as_of: Mapped[datetime]
    channel_ids: Mapped[list[str]] = text_array()
    competitor_set_version: Mapped[int]
    topic_ref_version: Mapped[int]
    business_profile_version: Mapped[int]
    gate_config_version: Mapped[str]
    analytics_config_hash: Mapped[str]
    record_counts: Mapped[dict[str, Any]] = jsonb_object()
    bundle_uri: Mapped[str | None]
    pit_level: Mapped[str]
    oldest_api_fetched_at: Mapped[datetime]
    expires_at: Mapped[datetime] = mapped_column(index=True)
    status: Mapped[str] = mapped_column(server_default=text("'active'"))
    purged_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = created_at_column()


class EvalPatternSpec(Base):
    __tablename__ = "eval_pattern_spec"
    __zone__ = Zone.evaluation
    __table_args__ = (Index("ix_eval_pattern_spec_eval_id_arm", "eval_id", "arm"),)

    eval_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("eval_config.eval_id", ondelete="CASCADE"), primary_key=True
    )
    spec_id: Mapped[str] = mapped_column(primary_key=True)
    workspace_id: Mapped[uuid.UUID | None]
    client_id: Mapped[uuid.UUID | None]
    arm: Mapped[str]
    replicate: Mapped[int | None]
    spec: Mapped[dict[str, Any]] = jsonb_object()
    spec_schema_version: Mapped[int] = schema_version_column()
    specificity: Mapped[int] = mapped_column(SmallInteger)
    source_ref: Mapped[str | None]
    channel_id: Mapped[str | None]
    expires_at: Mapped[datetime | None] = mapped_column(index=True)


class EvalOutcomeVideo(Base):
    __tablename__ = "eval_outcome_video"
    __zone__ = Zone.evaluation
    __table_args__ = (check_in("method", AGE_METHODS),)

    id: Mapped[uuid.UUID] = uuid_pk()
    eval_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("eval_config.eval_id", ondelete="CASCADE"), index=True
    )
    workspace_id: Mapped[uuid.UUID | None]
    client_id: Mapped[uuid.UUID | None]
    video_id: Mapped[str]
    channel_id: Mapped[str]
    published_at: Mapped[datetime]
    views_at_age: Mapped[int | None] = mapped_column(BigInteger)
    method: Mapped[str]
    baseline: Mapped[float | None] = mapped_column(REAL)
    ratio: Mapped[float | None] = mapped_column(REAL)
    is_outlier: Mapped[bool | None]
    matched: Mapped[dict[str, Any]] = jsonb_object()
    status: Mapped[str]
    expires_at: Mapped[datetime] = mapped_column(index=True)


class EvalResult(Base):
    __tablename__ = "eval_result"
    __zone__ = Zone.evaluation
    __table_args__ = (Index("ix_eval_result_eval_id_scope", "eval_id", "scope"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    eval_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("eval_config.eval_id", ondelete="CASCADE"))
    scope: Mapped[str]
    arm: Mapped[str]
    metric: Mapped[str]
    estimate: Mapped[float | None] = mapped_column(Double)
    ci_low: Mapped[float | None] = mapped_column(Double)
    ci_high: Mapped[float | None] = mapped_column(Double)
    n_videos: Mapped[int]
    n_channels: Mapped[int]
    p_value: Mapped[float | None] = mapped_column(Double)
    p_adj: Mapped[float | None] = mapped_column(Double)
    notes: Mapped[str | None]


class LabelTask(Base):
    __tablename__ = "label_task"
    __zone__ = Zone.evaluation

    task_id: Mapped[uuid.UUID] = uuid_pk()
    batch_id: Mapped[uuid.UUID] = mapped_column(index=True)
    video_id: Mapped[str | None]
    channel_id: Mapped[str | None]
    stratum: Mapped[str]
    inclusion_prob: Mapped[float] = mapped_column(Double)
    detector_ratio: Mapped[float | None] = mapped_column(REAL)
    detector_label: Mapped[bool]
    detector_version: Mapped[str]
    observed_at: Mapped[datetime]
    expires_at: Mapped[datetime] = mapped_column(index=True)


class Label(Base):
    __tablename__ = "label"
    __zone__ = Zone.evaluation

    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("label_task.task_id", ondelete="CASCADE"), primary_key=True
    )
    labeller_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    round: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    label: Mapped[str]
    reason_codes: Mapped[list[str]] = text_array()
    note: Mapped[str | None]
    seconds_spent: Mapped[int | None]
    labelled_at: Mapped[datetime]


class LabelGold(Base):
    __tablename__ = "label_gold"
    __zone__ = Zone.evaluation

    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("label_task.task_id", ondelete="CASCADE"), primary_key=True
    )
    gold: Mapped[str]
    method: Mapped[str]


class OutlierEvalResult(Base):
    __tablename__ = "outlier_eval_result"
    __zone__ = Zone.evaluation

    id: Mapped[uuid.UUID] = uuid_pk()
    batch_id: Mapped[uuid.UUID] = mapped_column(index=True)
    detector_version: Mapped[str]
    metrics: Mapped[dict[str, Any]] = jsonb_object()
    metrics_schema_version: Mapped[int] = schema_version_column()
    curve: Mapped[list[Any]] = jsonb_array()
    created_at: Mapped[datetime] = created_at_column()


class ReviewItem(Base):
    __tablename__ = "review_item"
    __zone__ = Zone.evaluation

    item_id: Mapped[uuid.UUID] = uuid_pk()
    batch_id: Mapped[uuid.UUID] = mapped_column(index=True)
    recommendation_id: Mapped[uuid.UUID | None]
    workspace_id: Mapped[uuid.UUID | None]
    client_id: Mapped[uuid.UUID | None]
    card_snapshot: Mapped[dict[str, Any] | None]
    card_snapshot_schema_version: Mapped[int] = schema_version_column()
    is_decoy: Mapped[bool]
    gate_features: Mapped[dict[str, Any]] = jsonb_object()
    display_seed: Mapped[int] = mapped_column(BigInteger)
    expires_at: Mapped[datetime] = mapped_column(index=True)


class ReviewRating(Base):
    __tablename__ = "review_rating"
    __zone__ = Zone.evaluation
    __table_args__ = tuple(
        CheckConstraint(f"{criterion} BETWEEN 1 AND 5", name=criterion) for criterion in REVIEW_CRITERIA
    )

    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("review_item.item_id", ondelete="CASCADE"), primary_key=True
    )
    rater_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    specific: Mapped[int] = mapped_column(SmallInteger)
    evidenced: Mapped[int] = mapped_column(SmallInteger)
    relevant: Mapped[int] = mapped_column(SmallInteger)
    actionable: Mapped[int] = mapped_column(SmallInteger)
    not_obvious: Mapped[int] = mapped_column(SmallInteger)
    would_ship: Mapped[bool]
    comment: Mapped[str | None]
    rated_at: Mapped[datetime]


class GateTuningProposal(Base):
    __tablename__ = "gate_tuning_proposal"
    __zone__ = Zone.evaluation

    id: Mapped[uuid.UUID] = uuid_pk()
    batch_id: Mapped[uuid.UUID] = mapped_column(index=True)
    feature: Mapped[str]
    rho: Mapped[float] = mapped_column(REAL)
    current_threshold: Mapped[float | None] = mapped_column(Double)
    proposed_threshold: Mapped[float | None] = mapped_column(Double)
    retained_share: Mapped[float] = mapped_column(REAL)
    retained_mean_rating: Mapped[float] = mapped_column(REAL)
    config_diff: Mapped[dict[str, Any]] = jsonb_object()
    rationale: Mapped[str]
    status: Mapped[str] = mapped_column(server_default=text("'proposed'"))
    decided_by: Mapped[uuid.UUID | None]
    decided_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = created_at_column()


class OpsDailyRollup(Base):
    __tablename__ = "ops_daily_rollup"
    __zone__ = Zone.global_
    __table_args__ = (
        Index(
            "uq_ops_daily_rollup_day_client_id",
            "day",
            "client_id",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    day: Mapped[date]
    workspace_id: Mapped[uuid.UUID | None]
    client_id: Mapped[uuid.UUID | None]
    metrics: Mapped[dict[str, Any]] = jsonb_object()
    metrics_schema_version: Mapped[int] = schema_version_column()
    updated_at: Mapped[datetime] = updated_at_column()


class AlertEvent(Base):
    __tablename__ = "alert_event"
    __zone__ = Zone.global_
    __table_args__ = (
        Index("ix_alert_event_rule_id_fired_at", "rule_id", "fired_at"),
        Index("ix_alert_event_rule_id_open", "rule_id", postgresql_where=text("resolved_at IS NULL")),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    rule_id: Mapped[str]
    severity: Mapped[str]
    value: Mapped[float] = mapped_column(Double)
    threshold: Mapped[float] = mapped_column(Double)
    fired_at: Mapped[datetime] = mapped_column(index=True)
    resolved_at: Mapped[datetime | None]
    payload: Mapped[dict[str, Any]] = jsonb_object()
