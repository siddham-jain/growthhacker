import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Identity, Index, Numeric, PrimaryKeyConstraint, SmallInteger, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Zone, check_in

QUOTA_BUCKETS = ("main", "search", "batch_stats", "analytics")


class YtQuotaLedger(Base):
    __tablename__ = "yt_quota_ledger"
    __zone__ = Zone.global_
    __table_args__ = (
        check_in("bucket", QUOTA_BUCKETS),
        Index("ix_yt_quota_ledger_pt_day_bucket", "pt_day", "bucket"),
        Index("ix_yt_quota_ledger_client_id_pt_day", "client_id", "pt_day"),
        Index("ix_yt_quota_ledger_run_id", "run_id"),
        Index("ix_yt_quota_ledger_occurred_at_brin", "occurred_at", postgresql_using="brin"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    request_id: Mapped[uuid.UUID] = mapped_column(unique=True)
    occurred_at: Mapped[datetime]
    pt_day: Mapped[date]
    project: Mapped[str] = mapped_column(server_default=text("'default'"))
    bucket: Mapped[str]
    endpoint: Mapped[str]
    units: Mapped[int]
    scope: Mapped[str]
    priority: Mapped[str | None]
    cache_outcome: Mapped[str | None]
    http_status: Mapped[int | None] = mapped_column(SmallInteger)
    reason: Mapped[str | None]
    cache_key: Mapped[str | None]
    workspace_id: Mapped[uuid.UUID | None]
    client_id: Mapped[uuid.UUID | None]
    run_id: Mapped[uuid.UUID | None]


class QuotaCacheHitsDaily(Base):
    __tablename__ = "quota_cache_hits_daily"
    __zone__ = Zone.global_
    __table_args__ = (PrimaryKeyConstraint("pt_day", "client_id", "data_kind"),)

    pt_day: Mapped[date]
    client_id: Mapped[uuid.UUID]
    data_kind: Mapped[str]
    hits: Mapped[int] = mapped_column(server_default=text("0"))


class ProviderUsageLedger(Base):
    __tablename__ = "provider_usage_ledger"
    __zone__ = Zone.global_
    __table_args__ = (
        check_in("provider_kind", ("llm", "tavily", "crawl", "embedding", "freshness")),
        Index("ix_provider_usage_ledger_client_id_occurred_at", "client_id", "occurred_at"),
        Index("ix_provider_usage_ledger_run_id", "run_id"),
        Index("ix_provider_usage_ledger_operation_occurred_at", "operation", "occurred_at"),
        Index(
            "ix_provider_usage_ledger_provider_model_resolved_occurred_at",
            "provider",
            "model_resolved",
            "occurred_at",
        ),
        Index("ix_provider_usage_ledger_occurred_at_brin", "occurred_at", postgresql_using="brin"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    request_id: Mapped[uuid.UUID] = mapped_column(unique=True)
    occurred_at: Mapped[datetime]
    provider_kind: Mapped[str]
    provider: Mapped[str]
    operation: Mapped[str]
    scope: Mapped[str | None]
    stage: Mapped[str | None]
    tier: Mapped[str | None]
    prompt_id: Mapped[str | None]
    prompt_version: Mapped[str | None]
    schema_name: Mapped[str | None]
    model_requested: Mapped[str | None]
    model_resolved: Mapped[str | None]
    route_index: Mapped[int | None] = mapped_column(SmallInteger)
    attempt_no: Mapped[int] = mapped_column(SmallInteger, server_default=text("1"))
    attempt_kind: Mapped[str | None]
    outcome: Mapped[str]
    error_kind: Mapped[str | None]
    status_code: Mapped[int | None]
    stop_reason: Mapped[str | None]
    refusal_category: Mapped[str | None]
    input_tokens: Mapped[int | None]
    output_tokens: Mapped[int | None]
    cache_read_tokens: Mapped[int | None]
    cache_write_tokens: Mapped[int | None]
    thinking_tokens: Mapped[int | None]
    credits: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    pages: Mapped[int | None]
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    price_missing: Mapped[bool] = mapped_column(server_default=text("false"))
    latency_ms: Mapped[int | None]
    provider_request_id: Mapped[str | None]
    input_hash: Mapped[str | None]
    output_hash: Mapped[str | None]
    cache_hit: Mapped[bool] = mapped_column(server_default=text("false"))
    degraded_route: Mapped[bool] = mapped_column(server_default=text("false"))
    workspace_id: Mapped[uuid.UUID | None]
    client_id: Mapped[uuid.UUID | None]
    run_id: Mapped[uuid.UUID | None]
