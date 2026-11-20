import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    REAL,
    CheckConstraint,
    ForeignKey,
    Index,
    PrimaryKeyConstraint,
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

VERIFICATION_STATUSES = ("active", "disputed", "retracted", "superseded")
SIGNAL_STATUSES = ("new", "drafted", "shown", "dismissed", "used", "suppressed", "expired")
URGENCIES = ("now", "this_week", "evergreen_window")


class NewsQuery(Base):
    __tablename__ = "news_queries"
    __zone__ = Zone.global_
    __table_args__ = (
        Index("ix_news_queries_next_run_at", "next_run_at", postgresql_where=text("subscriber_count > 0")),
        Index("ix_news_queries_orphaned_at", "orphaned_at", postgresql_where=text("subscriber_count = 0")),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    source_key: Mapped[str]
    normalized_query: Mapped[str]
    params: Mapped[dict[str, Any]] = jsonb_object()
    query_hash: Mapped[str] = mapped_column(unique=True)
    cadence_minutes: Mapped[int]
    next_run_at: Mapped[datetime]
    last_run_at: Mapped[datetime | None]
    last_status: Mapped[str | None]
    subscriber_count: Mapped[int] = mapped_column(server_default=text("0"))
    # set when subscriber_count drops to 0; the query is deleted 30 d later
    orphaned_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class NewsQuerySubscription(TenantColumns, Base):
    __tablename__ = "news_query_subscriptions"
    __zone__ = Zone.tenant
    __table_args__ = (client_fk(), PrimaryKeyConstraint("client_id", "query_id"))

    query_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("news_queries.id", ondelete="CASCADE"), index=True)
    origin: Mapped[str]
    weight: Mapped[float] = mapped_column(REAL)
    profile_version: Mapped[int]
    created_at: Mapped[datetime] = created_at_column()


class NewsQueryRun(Base):
    __tablename__ = "news_query_runs"
    __zone__ = Zone.global_
    __table_args__ = (
        Index("ix_news_query_runs_query_id_started_at", "query_id", text("started_at DESC")),
        Index("ix_news_query_runs_started_at", "started_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    query_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("news_queries.id", ondelete="CASCADE"))
    started_at: Mapped[datetime]
    finished_at: Mapped[datetime | None]
    status: Mapped[str]
    cost_credits: Mapped[Decimal | None]
    item_count: Mapped[int] = mapped_column(server_default=text("0"))
    error: Mapped[str | None]
    provider_request_id: Mapped[str | None]


class NewsItem(Base):
    __tablename__ = "news_items"
    __zone__ = Zone.global_
    __table_args__ = (
        CheckConstraint("char_length(snippet) <= 1500", name="snippet_length"),
        Index("ix_news_items_published_at", text("published_at DESC")),
        Index("ix_news_items_external_ids", "external_ids", postgresql_using="gin"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    source_key: Mapped[str]
    url: Mapped[str]
    canonical_url: Mapped[str]
    url_hash: Mapped[str] = mapped_column(unique=True)
    domain: Mapped[str] = mapped_column(index=True)
    title: Mapped[str]
    snippet: Mapped[str]
    language: Mapped[str | None]
    published_at: Mapped[datetime | None]
    published_precision: Mapped[str]
    fetched_at: Mapped[datetime] = mapped_column(index=True)
    source_rank_score: Mapped[float | None] = mapped_column(REAL)
    external_ids: Mapped[dict[str, Any]] = jsonb_object()
    source_tier: Mapped[str]
    flags: Mapped[list[str]] = text_array()
    story_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("news_stories.id", ondelete="SET NULL"), index=True
    )
    embedding: Mapped[bytes | None]


class NewsStory(Base):
    __tablename__ = "news_stories"
    __zone__ = Zone.global_
    __table_args__ = (Index("ix_news_stories_first_published_at", text("first_published_at DESC")),)

    id: Mapped[uuid.UUID] = uuid_pk()
    first_published_at: Mapped[datetime]
    last_published_at: Mapped[datetime] = mapped_column(index=True)
    representative_item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("news_items.id", ondelete="SET NULL", use_alter=True)
    )
    outlet_count: Mapped[int] = mapped_column(server_default=text("0"))
    best_tier: Mapped[str]
    version_tokens: Mapped[list[str]] = text_array()
    centroid: Mapped[bytes | None]
    status: Mapped[str] = mapped_column(index=True)


class SourceTier(Base):
    __tablename__ = "source_tiers"
    __zone__ = Zone.global_

    domain: Mapped[str] = mapped_column(primary_key=True)
    tier: Mapped[str]
    packs: Mapped[list[str]] = text_array()
    notes: Mapped[str | None]
    updated_by: Mapped[str]
    updated_at: Mapped[datetime] = updated_at_column()


class NewsJudgement(TenantColumns, Base):
    __tablename__ = "news_judgements"
    __zone__ = Zone.tenant
    __table_args__ = (
        client_fk(),
        UniqueConstraint("client_id", "story_id", "profile_version"),
        Index("ix_news_judgements_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    # no fk: global stories are purged at 90 d, tenant judgements are kept 180 d
    story_id: Mapped[uuid.UUID]
    profile_version: Mapped[int]
    model: Mapped[str]
    judgement: Mapped[dict[str, Any]] = mapped_column(JSONB)
    judgement_schema_version: Mapped[int] = schema_version_column()
    grounded: Mapped[bool]
    tokens_in: Mapped[int]
    tokens_out: Mapped[int]
    created_at: Mapped[datetime] = created_at_column()


class NewsSignal(TenantColumns, Base):
    __tablename__ = "news_signals"
    __zone__ = Zone.tenant
    __table_args__ = (
        client_fk(),
        check_in("verification_status", VERIFICATION_STATUSES),
        check_in("status", SIGNAL_STATUSES),
        check_in("urgency", URGENCIES),
        Index("ix_news_signals_client_id_status_expires_at", "client_id", "status", "expires_at"),
        Index("ix_news_signals_created_at", "created_at"),
        Index(
            "ix_news_signals_demand_expires_at",
            "demand_expires_at",
            postgresql_where=text("demand_expires_at IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    story_id: Mapped[uuid.UUID]
    judgement_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("news_judgements.id", ondelete="CASCADE"), index=True
    )
    profile_version: Mapped[int]
    entity: Mapped[str]
    aliases: Mapped[list[str]] = text_array()
    entity_kind: Mapped[str]
    is_competitor_entity: Mapped[bool] = mapped_column(server_default=text("false"))
    topic_ref_ids: Mapped[list[str]] = text_array()
    first_seen_at: Mapped[datetime]
    event_date: Mapped[date | None]
    sources: Mapped[list[Any]] = jsonb_array()
    credibility: Mapped[dict[str, Any]] = jsonb_object()
    demand: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    demand_check_id: Mapped[uuid.UUID | None]
    demand_video_ids: Mapped[list[str] | None]
    demand_expires_at: Mapped[datetime | None]
    verification_status: Mapped[str] = mapped_column(server_default=text("'active'"))
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("news_signals.id", ondelete="SET NULL")
    )
    relevance_score: Mapped[float] = mapped_column(REAL)
    urgency: Mapped[str]
    half_life_hours: Mapped[int]
    expires_at: Mapped[datetime]
    refresh_keywords: Mapped[list[str]] = text_array()
    refresh_eligible: Mapped[bool] = mapped_column(server_default=text("false"))
    safety: Mapped[dict[str, Any]] = jsonb_object()
    status: Mapped[str] = mapped_column(server_default=text("'new'"))
    suppression_reason: Mapped[str | None]
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class NewsReviewQueueItem(TenantColumns, Base):
    __tablename__ = "news_review_queue"
    __zone__ = Zone.tenant
    __table_args__ = (
        client_fk(),
        Index("ix_news_review_queue_client_id_decision", "client_id", "decision"),
        Index("ix_news_review_queue_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    signal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("news_signals.id", ondelete="CASCADE"), index=True
    )
    reason: Mapped[str]
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    decision: Mapped[str | None]
    decided_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = created_at_column()


class YtDemandCheck(Base):
    __tablename__ = "yt_demand_checks"
    __zone__ = Zone.shared
    __table_args__ = (UniqueConstraint("entity_norm", "day_bucket"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    entity_norm: Mapped[str]
    day_bucket: Mapped[date]
    query: Mapped[str]
    published_after: Mapped[datetime]
    video_ids: Mapped[list[str]] = text_array()
    early_videos: Mapped[list[Any]] = jsonb_array()
    aggregates: Mapped[dict[str, Any]] = jsonb_object()
    quota_units: Mapped[int] = mapped_column(server_default=text("0"))
    checked_at: Mapped[datetime] = mapped_column(index=True)
    status: Mapped[str]
