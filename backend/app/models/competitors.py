import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import REAL, ForeignKey, Index, Numeric, UniqueConstraint, func, text
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

COMPETITOR_STATUSES = (
    "suggested",
    "confirmed",
    "auto_confirmed",
    "rejected",
    "unresolved",
    "no_channel",
    "excluded",
    "user_added",
)
CANDIDATE_ROLES = ("competitor", "niche", "benchmark", "creator")
OWNERSHIP_LABELS = ("verified", "likely", "uncertain", "rejected")
OWNERSHIP_METHODS = ("oauth", "link", "user")


class CompetitorDiscoveryRun(TenantColumns, Base):
    __tablename__ = "competitor_discovery_runs"
    __zone__ = Zone.tenant
    __table_args__ = (
        client_fk(),
        Index(
            "ix_competitor_discovery_runs_client_id_started_at",
            "client_id",
            text("started_at DESC"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pipeline_runs.id", ondelete="SET NULL"), index=True
    )
    trigger: Mapped[str]
    status: Mapped[str] = mapped_column(server_default=text("'running'"))
    started_at: Mapped[datetime]
    finished_at: Mapped[datetime | None]
    yt_units: Mapped[int] = mapped_column(server_default=text("0"))
    search_calls: Mapped[int] = mapped_column(server_default=text("0"))
    tavily_credits: Mapped[int] = mapped_column(server_default=text("0"))
    llm_tokens_in: Mapped[int] = mapped_column(server_default=text("0"))
    llm_tokens_out: Mapped[int] = mapped_column(server_default=text("0"))
    llm_usd: Mapped[Decimal] = mapped_column(Numeric(8, 4), server_default=text("0"))
    warnings: Mapped[list[Any]] = jsonb_array()
    error: Mapped[str | None]


class CompetitorCandidate(TenantColumns, Base):
    __tablename__ = "competitor_candidates"
    __zone__ = Zone.tenant
    __table_args__ = (
        client_fk(),
        UniqueConstraint("client_id", "normalized_name"),
        check_in("role", CANDIDATE_ROLES, nullable=True),
        check_in("status", COMPETITOR_STATUSES),
        Index(
            "uq_competitor_candidates_client_id_official_domain",
            "client_id",
            "official_domain",
            unique=True,
            postgresql_where=text("official_domain IS NOT NULL"),
        ),
        Index("ix_competitor_candidates_client_id_role_status", "client_id", "role", "status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    brand_name: Mapped[str]
    normalized_name: Mapped[str]
    aliases: Mapped[list[str]] = text_array()
    official_domain: Mapped[str | None]
    relation: Mapped[str]
    owner_kind: Mapped[str] = mapped_column(server_default=text("'unknown'"))
    role: Mapped[str | None]
    geography: Mapped[list[Any]] = jsonb_array()
    business: Mapped[dict[str, Any]] = jsonb_object()
    business_schema_version: Mapped[int] = schema_version_column()
    confidence: Mapped[str]
    status: Mapped[str] = mapped_column(server_default=text("'suggested'"))
    flags: Mapped[list[str]] = text_array()
    evidence_summary: Mapped[str | None]
    decided_by: Mapped[str | None]
    decided_at: Mapped[datetime | None]
    rejection_reason: Mapped[str | None]
    first_seen_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("competitor_discovery_runs.id", ondelete="SET NULL")
    )
    last_seen_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("competitor_discovery_runs.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class CompetitorCandidateSource(TenantColumns, Base):
    __tablename__ = "competitor_candidate_sources"
    __zone__ = Zone.tenant
    __table_args__ = (
        client_fk(),
        Index("ix_competitor_candidate_sources_candidate_id", "candidate_id"),
        Index("ix_competitor_candidate_sources_retrieved_at", "retrieved_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("competitor_candidates.id", ondelete="CASCADE")
    )
    discovery_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("competitor_discovery_runs.id", ondelete="SET NULL")
    )
    kind: Mapped[str]
    ref: Mapped[str]
    snippet: Mapped[str | None]
    retrieved_at: Mapped[datetime]


class CompetitorChannelMatch(TenantColumns, Base):
    __tablename__ = "competitor_channel_matches"
    __zone__ = Zone.tenant
    __table_args__ = (
        client_fk(),
        UniqueConstraint("candidate_id", "channel_id"),
        check_in("ownership_label", OWNERSHIP_LABELS),
        check_in("ownership_method", OWNERSHIP_METHODS, nullable=True),
        Index("ix_competitor_channel_matches_channel_id", "channel_id"),
        Index("ix_competitor_channel_matches_youtube_fetched_at", "youtube_fetched_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("competitor_candidates.id", ondelete="CASCADE")
    )
    channel_id: Mapped[str]
    resolution_method: Mapped[str]
    variant: Mapped[str] = mapped_column(server_default=text("'unknown'"))
    is_primary: Mapped[bool] = mapped_column(server_default=text("false"))
    is_tracked: Mapped[bool] = mapped_column(server_default=text("false"))
    ownership_label: Mapped[str]
    ownership_score: Mapped[float] = mapped_column(REAL)
    ownership_method: Mapped[str | None]
    ownership_signals: Mapped[list[Any]] = jsonb_array()
    llm_reason: Mapped[str | None]
    youtube_evidence: Mapped[dict[str, Any] | None]
    youtube_evidence_schema_version: Mapped[int] = schema_version_column()
    youtube_fetched_at: Mapped[datetime | None]
    flags: Mapped[list[str]] = text_array()


class CompetitorSet(TenantColumns, Base):
    __tablename__ = "competitor_sets"
    __zone__ = Zone.tenant
    __table_args__ = (
        client_fk(),
        UniqueConstraint("client_id", "version"),
        Index("ix_competitor_sets_ids_verified_at", "ids_verified_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    version: Mapped[int]
    competitor_channel_ids: Mapped[list[str]] = text_array()
    niche_channel_ids: Mapped[list[str]] = text_array()
    benchmark_channel_ids: Mapped[list[str]] = text_array()
    creator_seed_channel_ids: Mapped[list[str]] = text_array()
    contains_content_competitors: Mapped[bool] = mapped_column(server_default=text("false"))
    confirmed_by: Mapped[str]
    confirmed_at: Mapped[datetime]
    ids_verified_at: Mapped[datetime] = mapped_column(server_default=func.now())


class BrandChannelResolution(Base):
    __tablename__ = "brand_channel_resolutions"
    __zone__ = Zone.shared
    __table_args__ = (
        UniqueConstraint("key_type", "key", "channel_id"),
        check_in("ownership_label", OWNERSHIP_LABELS),
        check_in("ownership_method", OWNERSHIP_METHODS, nullable=True),
        Index("ix_brand_channel_resolutions_expires_at", "expires_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    key_type: Mapped[str]
    key: Mapped[str]
    channel_id: Mapped[str]
    ownership_label: Mapped[str]
    ownership_score: Mapped[float] = mapped_column(REAL)
    ownership_method: Mapped[str | None]
    signals: Mapped[list[Any]] = jsonb_array()
    verified_at: Mapped[datetime]
    expires_at: Mapped[datetime]


class CompetitorDriftEvent(TenantColumns, Base):
    __tablename__ = "competitor_drift_events"
    __zone__ = Zone.tenant
    __table_args__ = (
        client_fk(),
        Index(
            "ix_competitor_drift_events_client_id_acknowledged_at",
            "client_id",
            text("acknowledged_at NULLS FIRST"),
            text("detected_at DESC"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("competitor_candidates.id", ondelete="CASCADE")
    )
    channel_id: Mapped[str | None]
    kind: Mapped[str]
    detail: Mapped[dict[str, Any]] = jsonb_object()
    detected_at: Mapped[datetime]
    acknowledged_at: Mapped[datetime | None]
