import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    REAL,
    BigInteger,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    PrimaryKeyConstraint,
    SmallInteger,
    UniqueConstraint,
    func,
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
    text_array,
    uuid_pk,
)

RUN_KINDS = ("onboarding", "analysis", "daily_refresh", "cards", "news_scan", "recrawl", "backtest")
RUN_TRIGGERS = ("user", "schedule", "system", "resume", "evaluation")
RUN_STATUSES = (
    "queued",
    "running",
    "cancelling",
    "succeeded",
    "partial",
    "failed",
    "cancelled",
    "superseded",
)
ACTIVE_RUN_STATUSES = ("queued", "running", "cancelling")
STAGE_STATUSES = (
    "pending",
    "running",
    "succeeded",
    "reused",
    "degraded",
    "failed",
    "skipped",
    "cancelled",
    "interrupted",
)
RUN_EVENT_TYPES = (
    "run.queued",
    "run.started",
    "run.progress",
    "run.awaiting_confirmation",
    "run.succeeded",
    "run.partial",
    "run.failed",
    "run.cancelled",
    "run.superseded",
    "stage.started",
    "stage.step",
    "stage.progress",
    "stage.retrying",
    "stage.reused",
    "stage.succeeded",
    "stage.degraded",
    "stage.failed",
    "stage.skipped",
    "insight",
    "notice",
    "results.published",
)
EVENT_LEVELS = ("info", "warning", "error")
SCHEDULE_KINDS = ("daily_refresh", "news_scan")
MAX_ARTIFACT_PAYLOAD_BYTES = 2 * 1024 * 1024

_ACTIVE_STATUS_SQL = "status IN ('queued', 'running', 'cancelling')"


class PipelineRun(TenantColumns, Base):
    __tablename__ = "pipeline_runs"
    __zone__ = Zone.tenant
    __table_args__ = (
        client_fk(),
        check_in("kind", RUN_KINDS),
        check_in("trigger", RUN_TRIGGERS),
        check_in("status", RUN_STATUSES),
        CheckConstraint("window_days IS NULL OR window_days IN (7, 30, 90)", name="window_days"),
        Index(
            "uq_pipeline_runs_active_per_client",
            "client_id",
            unique=True,
            postgresql_where=text(_ACTIVE_STATUS_SQL),
        ),
        Index(
            "uq_pipeline_runs_workspace_id_idempotency_key",
            "workspace_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        Index("ix_pipeline_runs_client_id_created_at", "client_id", text("created_at DESC")),
        Index(
            "ix_pipeline_runs_status_lease_expires_at",
            "status",
            "lease_expires_at",
            postgresql_where=text(_ACTIVE_STATUS_SQL),
        ),
        Index(
            "ix_pipeline_runs_workspace_id_active", "workspace_id", postgresql_where=text(_ACTIVE_STATUS_SQL)
        ),
        Index("ix_pipeline_runs_finished_at", "finished_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    kind: Mapped[str]
    trigger: Mapped[str]
    status: Mapped[str] = mapped_column(server_default=text("'queued'"))
    attempt: Mapped[int] = mapped_column(SmallInteger, server_default=text("1"))
    resume_of_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pipeline_runs.id", ondelete="SET NULL")
    )
    superseded_by_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pipeline_runs.id", ondelete="SET NULL")
    )
    follow_up_requested: Mapped[bool] = mapped_column(server_default=text("false"))
    window_days: Mapped[int | None] = mapped_column(SmallInteger)
    as_of: Mapped[datetime] = mapped_column(server_default=func.now())
    config_snapshot: Mapped[dict[str, Any]] = jsonb_object()
    config_hash: Mapped[str]
    params_hash: Mapped[str | None]
    params: Mapped[dict[str, Any]] = jsonb_object()
    queue: Mapped[str]
    priority: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
    arq_job_id: Mapped[str | None]
    lease_owner: Mapped[str | None]
    lease_expires_at: Mapped[datetime | None]
    event_seq: Mapped[int] = mapped_column(server_default=text("0"))
    progress_pct: Mapped[float] = mapped_column(REAL, server_default=text("0"))
    current_stages: Mapped[list[str]] = text_array()
    pinned_channel_ids: Mapped[list[str]] = text_array()
    notices: Mapped[list[Any]] = jsonb_array()
    usage: Mapped[dict[str, Any]] = jsonb_object()
    budget: Mapped[dict[str, Any]] = jsonb_object()
    timings: Mapped[dict[str, Any]] = jsonb_object()
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    idempotency_key: Mapped[str | None]
    requested_by: Mapped[str | None]
    created_at: Mapped[datetime] = created_at_column()
    queued_at: Mapped[datetime] = created_at_column()
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    cancel_requested_at: Mapped[datetime | None]


class StageRun(TenantColumns, Base):
    __tablename__ = "stage_runs"
    __zone__ = Zone.tenant
    __table_args__ = (
        client_fk(),
        PrimaryKeyConstraint("run_id", "stage"),
        check_in("status", STAGE_STATUSES),
        Index("ix_stage_runs_stage_finished_at", "stage", "finished_at"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pipeline_runs.id", ondelete="CASCADE"))
    stage: Mapped[str]
    stage_version: Mapped[int] = mapped_column(server_default=text("1"))
    status: Mapped[str] = mapped_column(server_default=text("'pending'"))
    attempt: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
    input_fingerprint: Mapped[str | None]
    reused_from_run_id: Mapped[uuid.UUID | None]
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    duration_ms: Mapped[int | None]
    counts: Mapped[dict[str, Any]] = jsonb_object()
    usage: Mapped[dict[str, Any]] = jsonb_object()
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    warnings: Mapped[list[Any]] = jsonb_array()


class RunArtifact(TenantColumns, Base):
    __tablename__ = "run_artifacts"
    __zone__ = Zone.tenant
    __table_args__ = (
        client_fk(),
        UniqueConstraint("run_id", "key"),
        CheckConstraint(f"pg_column_size(payload) < {MAX_ARTIFACT_PAYLOAD_BYTES}", name="payload_size"),
        Index(
            "ix_run_artifacts_client_id_key_fingerprint_created_at",
            "client_id",
            "key",
            "fingerprint",
            text("created_at DESC"),
        ),
        Index("ix_run_artifacts_referenced_video_ids", "referenced_video_ids", postgresql_using="gin"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pipeline_runs.id", ondelete="CASCADE"))
    key: Mapped[str]
    schema_version: Mapped[int]
    fingerprint: Mapped[str]
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    contains_api_data: Mapped[bool]
    uses_authorized_data: Mapped[bool] = mapped_column(server_default=text("false"))
    referenced_video_ids: Mapped[list[str]] = text_array()
    referenced_channel_ids: Mapped[list[str]] = text_array()
    api_data_observed_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = created_at_column()
    expires_at: Mapped[datetime] = mapped_column(index=True)


class RunArtifactBlob(TenantColumns, Base):
    """gzipped payloads of artifacts over the inline cap; run_artifacts rows point here by content hash."""

    __tablename__ = "run_artifact_blobs"
    __zone__ = Zone.tenant
    __table_args__ = (
        client_fk(),
        PrimaryKeyConstraint("client_id", "sha256"),
    )

    sha256: Mapped[str]
    payload_gz: Mapped[bytes]
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = created_at_column()
    expires_at: Mapped[datetime] = mapped_column(index=True)


class RunEvent(TenantColumns, Base):
    __tablename__ = "run_events"
    __zone__ = Zone.tenant
    __table_args__ = (
        client_fk(),
        UniqueConstraint("run_id", "seq"),
        check_in("type", RUN_EVENT_TYPES),
        check_in("level", EVENT_LEVELS),
        CheckConstraint("char_length(message) <= 200", name="message_length"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pipeline_runs.id", ondelete="CASCADE"))
    seq: Mapped[int]
    type: Mapped[str]
    stage: Mapped[str | None]
    step: Mapped[str | None]
    status: Mapped[str | None]
    level: Mapped[str] = mapped_column(server_default=text("'info'"))
    message: Mapped[str]
    counts: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    percent: Mapped[float | None] = mapped_column(REAL)
    stage_percent: Mapped[float | None] = mapped_column(REAL)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(server_default=func.clock_timestamp(), index=True)


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    __zone__ = Zone.workspace
    __table_args__ = (PrimaryKeyConstraint("workspace_id", "key"),)

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    key: Mapped[str]
    fingerprint: Mapped[str]
    method: Mapped[str]
    path: Mapped[str]
    status: Mapped[str]
    response_status: Mapped[int | None] = mapped_column(SmallInteger)
    response_headers: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    response_body: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    resource_id: Mapped[str | None]
    created_at: Mapped[datetime] = created_at_column()
    expires_at: Mapped[datetime] = mapped_column(index=True)


class ClientSchedule(TenantColumns, Base):
    __tablename__ = "client_schedules"
    __zone__ = Zone.tenant
    __table_args__ = (
        client_fk(),
        PrimaryKeyConstraint("client_id", "kind"),
        check_in("kind", SCHEDULE_KINDS),
        Index(
            "ix_client_schedules_kind_next_due_at", "kind", "next_due_at", postgresql_where=text("NOT paused")
        ),
    )

    kind: Mapped[str]
    cadence_hours: Mapped[float] = mapped_column(REAL)
    next_due_at: Mapped[datetime | None]
    last_enqueued_at: Mapped[datetime | None]
    last_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("pipeline_runs.id", ondelete="SET NULL"))
    last_success_at: Mapped[datetime | None]
    failure_streak: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
    circuit_open_until: Mapped[datetime | None]
    paused: Mapped[bool] = mapped_column(server_default=text("false"))


class ActiveResultSet(TenantColumns, Base):
    __tablename__ = "active_result_sets"
    __zone__ = Zone.tenant
    __table_args__ = (
        client_fk(),
        PrimaryKeyConstraint("client_id", "window_days"),
        CheckConstraint("window_days IN (7, 30, 90)", name="window_days"),
        ForeignKeyConstraint(["run_id"], ["pipeline_runs.id"], ondelete="CASCADE"),
    )

    window_days: Mapped[int] = mapped_column(SmallInteger)
    run_id: Mapped[uuid.UUID]
    published_at: Mapped[datetime]
    stale_reasons: Mapped[list[str]] = text_array()
    superseded_history: Mapped[list[Any]] = jsonb_array()
