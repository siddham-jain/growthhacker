import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    REAL,
    CheckConstraint,
    ForeignKey,
    Index,
    PrimaryKeyConstraint,
    SmallInteger,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    Base,
    TenantColumns,
    Zone,
    check_in,
    client_fk,
    created_at_column,
    jsonb_array,
    jsonb_object,
    schema_version_column,
    text_array,
    updated_at_column,
    uuid_pk,
)

RECOMMENDATION_TYPES = (
    "video_idea",
    "title_pattern",
    "thumbnail_pattern",
    "format",
    "creator",
    "news_opportunity",
    "description_refresh",
)
RECOMMENDATION_STATUSES = ("shown", "expired", "hidden")
CHANNEL_ROLES = ("own", "competitor", "niche", "benchmark", "creator")
FEEDBACK_STATUSES = ("saved", "dismissed", "done", "snoozed")


class Recommendation(TenantColumns, Base):
    __tablename__ = "recommendations"
    __zone__ = Zone.tenant
    __table_args__ = (
        client_fk(),
        UniqueConstraint("run_id", "stable_key"),
        check_in("type", RECOMMENDATION_TYPES),
        check_in("status", RECOMMENDATION_STATUSES),
        CheckConstraint("window_days IN (7, 30, 90)", name="window_days"),
        Index("ix_recommendations_client_id_window_days_status", "client_id", "window_days", "status"),
        Index("ix_recommendations_client_id_stable_key", "client_id", "stable_key"),
        Index("ix_recommendations_client_id_rec_id", "client_id", "rec_id"),
        Index("ix_recommendations_referenced_video_ids", "referenced_video_ids", postgresql_using="gin"),
        Index("ix_recommendations_referenced_channel_ids", "referenced_channel_ids", postgresql_using="gin"),
        Index(
            "ix_recommendations_api_data_observed_at",
            "api_data_observed_at",
            postgresql_where=text("redacted_at IS NULL"),
        ),
        Index("ix_recommendations_expires_at", "expires_at", postgresql_where=text("status = 'shown'")),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    rec_id: Mapped[str]
    stable_key: Mapped[str]
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pipeline_runs.id", ondelete="CASCADE"))
    type: Mapped[str]
    window_days: Mapped[int] = mapped_column(SmallInteger)
    rank: Mapped[int | None] = mapped_column(SmallInteger)
    status: Mapped[str] = mapped_column(server_default=text("'shown'"))
    lens: Mapped[list[str]]
    buyer_question_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("buyer_questions.id", ondelete="SET NULL")
    )
    score_total: Mapped[float | None] = mapped_column(REAL)
    score_components: Mapped[dict[str, Any]] = jsonb_object()
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    payload_schema_version: Mapped[int] = schema_version_column()
    draft: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    draft_schema_version: Mapped[int] = schema_version_column()
    writer_meta: Mapped[dict[str, Any]] = jsonb_object()
    referenced_video_ids: Mapped[list[str]] = text_array()
    referenced_channel_ids: Mapped[list[str]] = text_array()
    stale_reference_count: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
    uses_authorized_data: Mapped[bool] = mapped_column(server_default=text("false"))
    first_seen_at: Mapped[datetime]
    last_seen_at: Mapped[datetime]
    generated_at: Mapped[datetime]
    api_data_observed_at: Mapped[datetime]
    expires_at: Mapped[datetime]
    redacted_at: Mapped[datetime | None]


class RecommendationReference(TenantColumns, Base):
    __tablename__ = "recommendation_references"
    __zone__ = Zone.tenant
    __table_args__ = (
        client_fk(),
        PrimaryKeyConstraint("recommendation_id", "ordinal"),
        check_in("channel_role", CHANNEL_ROLES),
    )

    recommendation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recommendations.id", ondelete="CASCADE"))
    ordinal: Mapped[int] = mapped_column(SmallInteger)
    video_id: Mapped[str] = mapped_column(index=True)
    channel_id: Mapped[str]
    channel_role: Mapped[str]


class RecommendationSuppression(TenantColumns, Base):
    __tablename__ = "recommendation_suppressions"
    __zone__ = Zone.tenant
    __table_args__ = (
        client_fk(),
        check_in("type", RECOMMENDATION_TYPES),
        Index("ix_recommendation_suppressions_client_id_created_at", "client_id", "created_at"),
        Index("ix_recommendation_suppressions_gate_reason_code", "gate", "reason_code"),
        Index(
            "ix_recommendation_suppressions_created_at",
            "created_at",
            postgresql_where=text("draft IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    # set null so gate counts outlive the run (99 §5: counts kept)
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pipeline_runs.id", ondelete="SET NULL"), index=True
    )
    type: Mapped[str]
    stable_key: Mapped[str]
    phase: Mapped[str]
    gate: Mapped[str]
    reason_code: Mapped[str]
    details: Mapped[dict[str, Any]] = jsonb_object()
    draft: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    draft_schema_version: Mapped[int] = schema_version_column()
    created_at: Mapped[datetime] = created_at_column()


class RecommendationNotice(TenantColumns, Base):
    __tablename__ = "recommendation_notices"
    __zone__ = Zone.tenant
    __table_args__ = (
        client_fk(),
        check_in("rec_type", RECOMMENDATION_TYPES, nullable=True),
        CheckConstraint("window_days IN (7, 30, 90)", name="window_days"),
        Index("ix_recommendation_notices_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pipeline_runs.id", ondelete="CASCADE"), index=True)
    window_days: Mapped[int] = mapped_column(SmallInteger)
    kind: Mapped[str]
    rec_type: Mapped[str | None]
    payload: Mapped[dict[str, Any]] = jsonb_object()
    payload_schema_version: Mapped[int] = schema_version_column()
    created_at: Mapped[datetime] = created_at_column()


class WhiteSpaceNote(TenantColumns, Base):
    __tablename__ = "white_space_notes"
    __zone__ = Zone.tenant
    __table_args__ = (
        client_fk(),
        Index("ix_white_space_notes_client_id_run_id", "client_id", "run_id"),
        Index("ix_white_space_notes_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pipeline_runs.id", ondelete="CASCADE"))
    buyer_question_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("buyer_questions.id", ondelete="CASCADE"))
    payload: Mapped[dict[str, Any]] = jsonb_object()
    payload_schema_version: Mapped[int] = schema_version_column()
    created_at: Mapped[datetime] = created_at_column()


class RecommendationFeedback(TenantColumns, Base):
    __tablename__ = "recommendation_feedback"
    __zone__ = Zone.tenant
    __table_args__ = (
        client_fk(),
        UniqueConstraint("client_id", "stable_key", "user_id"),
        check_in("status", FEEDBACK_STATUSES, nullable=True),
        Index("ix_recommendation_feedback_recommendation_id", "recommendation_id"),
        Index("ix_recommendation_feedback_client_id_created_at", "client_id", "created_at"),
        Index(
            "ix_recommendation_feedback_linked_video_verified_at",
            "linked_video_verified_at",
            postgresql_where=text("linked_video_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    recommendation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("recommendations.id", ondelete="SET NULL")
    )
    stable_key: Mapped[str]
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    status: Mapped[str | None]
    reason: Mapped[str | None]
    linked_video_id: Mapped[str | None]
    linked_video_verified_at: Mapped[datetime | None]
    note: Mapped[str | None]
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class RecommendationOutcome(TenantColumns, Base):
    __tablename__ = "recommendation_outcomes"
    __zone__ = Zone.tenant
    __table_args__ = (
        client_fk(),
        Index("ix_recommendation_outcomes_client_id_stable_key", "client_id", "stable_key"),
        Index(
            "ix_recommendation_outcomes_measured_at",
            "measured_at",
            postgresql_where=text("observed_ratio IS NOT NULL"),
        ),
        Index(
            "ix_recommendation_outcomes_video_verified_at",
            "video_verified_at",
            postgresql_where=text("video_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    stable_key: Mapped[str]
    video_id: Mapped[str | None]
    video_verified_at: Mapped[datetime | None]
    matched_by: Mapped[str]
    comparison_age_days: Mapped[int] = mapped_column(SmallInteger)
    predicted_low: Mapped[float | None] = mapped_column(REAL)
    predicted_high: Mapped[float | None] = mapped_column(REAL)
    observed_ratio: Mapped[float | None] = mapped_column(REAL)
    within_range: Mapped[bool | None]
    became_outlier: Mapped[bool | None]
    measured_at: Mapped[datetime]


class CreatorCollaboration(Base):
    __tablename__ = "creator_collaborations"
    __zone__ = Zone.shared
    __table_args__ = (
        PrimaryKeyConstraint("creator_channel_id", "other_channel_id", "video_id", "kind"),
        Index("ix_creator_collaborations_detected_at", "detected_at"),
    )

    creator_channel_id: Mapped[str]
    other_channel_id: Mapped[str] = mapped_column(index=True)
    video_id: Mapped[str]
    kind: Mapped[str]
    matched_on: Mapped[str]
    matched_token: Mapped[str]
    video_published_at: Mapped[datetime]
    detected_at: Mapped[datetime] = created_at_column()


class ClientRecommendConfig(TenantColumns, Base):
    __tablename__ = "client_recommend_config"
    __zone__ = Zone.tenant
    __table_args__ = (client_fk(), PrimaryKeyConstraint("client_id"))

    lens_mode: Mapped[str] = mapped_column(server_default=text("'both'"))
    recommend_overrides: Mapped[dict[str, Any]] = jsonb_object()
    excluded_formats: Mapped[list[str]] = text_array()
    excluded_creators: Mapped[list[Any]] = jsonb_array()
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()
