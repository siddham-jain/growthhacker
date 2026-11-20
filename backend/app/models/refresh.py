import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    REAL,
    CheckConstraint,
    ForeignKey,
    Index,
    PrimaryKeyConstraint,
    SmallInteger,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    Base,
    TenantColumns,
    Zone,
    check_in,
    client_fk,
    jsonb_array,
    jsonb_object,
    schema_version_column,
    text_array,
    updated_at_column,
    uuid_pk,
)

REFRESH_STATUSES = (
    "proposed",
    "dismissed",
    "applied_claimed",
    "applied_detected",
    "expired",
    "withdrawn",
    "superseded",
)


class RefreshEligibility(TenantColumns, Base):
    __tablename__ = "refresh_eligibility"
    __zone__ = Zone.tenant
    __table_args__ = (
        client_fk(),
        Index("ix_refresh_eligibility_client_id_computed_at", "client_id", text("computed_at DESC")),
        Index("ix_refresh_eligibility_computed_at", "computed_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pipeline_runs.id", ondelete="CASCADE"), index=True)
    decision: Mapped[str]
    cadence_days: Mapped[int] = mapped_column(SmallInteger)
    keyword_refresh_need: Mapped[float] = mapped_column(REAL)
    components: Mapped[dict[str, Any]] = jsonb_object()
    evergreen_video_count: Mapped[int]
    eligible_video_count: Mapped[int]
    velocity_basis: Mapped[str]
    reasons: Mapped[list[Any]] = jsonb_array()
    computed_at: Mapped[datetime]
    last_keyword_eval_at: Mapped[datetime | None]


class DescriptionRefresh(TenantColumns, Base):
    __tablename__ = "description_refreshes"
    __zone__ = Zone.tenant
    __table_args__ = (
        client_fk(),
        check_in("status", REFRESH_STATUSES),
        CheckConstraint("resulting_bytes <= 5000", name="resulting_bytes"),
        Index(
            "ix_description_refreshes_client_id_status_priority", "client_id", "status", text("priority DESC")
        ),
        Index("ix_description_refreshes_client_id_video_id_status", "client_id", "video_id", "status"),
        Index("ix_description_refreshes_dedupe_keys", "dedupe_keys", postgresql_using="gin"),
        Index("ix_description_refreshes_expires_at", "expires_at"),
        Index(
            "ix_description_refreshes_generated_at",
            "generated_at",
            postgresql_where=text("redacted_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    video_id: Mapped[str]
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pipeline_runs.id", ondelete="CASCADE"), index=True)
    recommendation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("recommendations.id", ondelete="SET NULL")
    )
    triggers: Mapped[list[Any]] = jsonb_array()
    operations: Mapped[list[Any]] = jsonb_array()
    operations_schema_version: Mapped[int] = schema_version_column()
    advisories: Mapped[list[Any]] = jsonb_array()
    suggested_tags: Mapped[list[str]] = text_array()
    keywords_added: Mapped[list[str]] = text_array()
    dedupe_keys: Mapped[list[str]] = text_array()
    inserted_bytes: Mapped[int]
    resulting_bytes: Mapped[int]
    expected_effect: Mapped[str]
    confidence: Mapped[dict[str, Any]] = jsonb_object()
    priority: Mapped[float] = mapped_column(REAL)
    expires_at: Mapped[datetime]
    status: Mapped[str] = mapped_column(server_default=text("'proposed'"))
    description_hash_at_generation: Mapped[str]
    review_required: Mapped[bool] = mapped_column(server_default=text("false"))
    generated_at: Mapped[datetime]
    applied_at: Mapped[date | None]
    applied_source: Mapped[str | None]
    dismissed_reason: Mapped[str | None]
    redacted_at: Mapped[datetime | None]
    updated_at: Mapped[datetime] = updated_at_column()


class RefreshImpact(TenantColumns, Base):
    __tablename__ = "refresh_impact"
    __zone__ = Zone.tenant
    __table_args__ = (client_fk(), PrimaryKeyConstraint("refresh_id"))

    refresh_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("description_refreshes.id", ondelete="CASCADE"))
    applied_at: Mapped[date]
    applied_source: Mapped[str]
    pre_window_days: Mapped[int] = mapped_column(SmallInteger)
    post_window_days: Mapped[int] = mapped_column(SmallInteger)
    pre_velocity: Mapped[float] = mapped_column(REAL)
    post_velocity: Mapped[float] = mapped_column(REAL)
    ratio: Mapped[float] = mapped_column(REAL)
    control_ratio: Mapped[float | None] = mapped_column(REAL)
    adjusted_ratio: Mapped[float | None] = mapped_column(REAL)
    owner_search_views_pre: Mapped[int | None]
    owner_search_views_post: Mapped[int | None]
    verdict: Mapped[str]
    caveats: Mapped[list[Any]] = jsonb_array()
    computed_at: Mapped[datetime] = mapped_column(index=True)


class LinkCheckCache(Base):
    __tablename__ = "link_check_cache"
    __zone__ = Zone.global_

    url_normalised: Mapped[str] = mapped_column(primary_key=True)
    status_class: Mapped[str]
    http_status: Mapped[int | None] = mapped_column(SmallInteger)
    final_url: Mapped[str | None]
    error: Mapped[str | None]
    checked_at: Mapped[datetime] = mapped_column(index=True)
    next_check_at: Mapped[datetime] = mapped_column(index=True)
