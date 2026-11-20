import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    REAL,
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    PrimaryKeyConstraint,
    SmallInteger,
    UniqueConstraint,
)
from sqlalchemy import text as sql_text
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

SOURCE_KINDS = (
    "site_direct",
    "site_rendered",
    "reader_proxy",
    "search_index",
    "archive",
    "knowledge_base",
    "web_mentions",
    "youtube_channel",
    "user",
)
PROFILE_STATUSES = ("draft", "awaiting_confirmation", "confirmed", "superseded")
ITEM_STATUSES = ("proposed", "confirmed", "rejected", "retired")
FUNNEL_STAGES = ("awareness", "consideration", "decision", "post_purchase")
BUYER_QUESTION_SOURCES = ("website_faq", "website_derived", "user", "spotlit", "comments", "import")
STORED_TEXT_CHAR_CAP = 60_000


class Website(TenantColumns, Base):
    __tablename__ = "websites"
    __zone__ = Zone.tenant
    __table_args__ = (
        client_fk(),
        UniqueConstraint(
            "client_id",
            "registrable_domain",
            "focus_path",
            postgresql_nulls_not_distinct=True,
        ),
        Index("ix_websites_registrable_domain", "registrable_domain"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    input_url: Mapped[str]
    canonical_origin: Mapped[str]
    registrable_domain: Mapped[str]
    scope_hosts: Mapped[list[str]] = text_array()
    scope_mode: Mapped[str]
    focus_path: Mapped[str | None]
    country_sites: Mapped[list[Any]] = jsonb_array()
    related_brands: Mapped[list[Any]] = jsonb_array()
    ownership_attested: Mapped[bool] = mapped_column(server_default=sql_text("false"))
    robots_status: Mapped[str | None]
    last_crawl_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("crawl_runs.id", ondelete="SET NULL", use_alter=True)
    )
    created_at: Mapped[datetime] = created_at_column()


class CrawlRun(TenantColumns, Base):
    __tablename__ = "crawl_runs"
    __zone__ = Zone.tenant
    __table_args__ = (
        client_fk(),
        Index("ix_crawl_runs_website_id_started_at", "website_id", sql_text("started_at DESC")),
        Index("ix_crawl_runs_finished_at", "finished_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    website_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("websites.id", ondelete="CASCADE"))
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pipeline_runs.id", ondelete="SET NULL"), index=True
    )
    trigger: Mapped[str]
    profile: Mapped[str]
    status: Mapped[str] = mapped_column(server_default=sql_text("'running'"))
    ua_mode: Mapped[str | None]
    budget: Mapped[dict[str, Any]] = jsonb_object()
    stats: Mapped[dict[str, Any]] = jsonb_object()
    error: Mapped[str | None]
    started_at: Mapped[datetime]
    finished_at: Mapped[datetime | None]


class CrawlFrontierEntry(TenantColumns, Base):
    __tablename__ = "crawl_frontier"
    __zone__ = Zone.tenant
    __table_args__ = (client_fk(),)

    crawl_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("crawl_runs.id", ondelete="CASCADE"), primary_key=True
    )
    url_hash: Mapped[str] = mapped_column(primary_key=True)
    url: Mapped[str]
    source: Mapped[str]
    depth: Mapped[int] = mapped_column(SmallInteger)
    page_type_guess: Mapped[str | None]
    template_key: Mapped[str | None]
    score: Mapped[float] = mapped_column(REAL)
    state: Mapped[str] = mapped_column(server_default=sql_text("'queued'"))
    reason: Mapped[str | None]
    sitemap_lastmod: Mapped[datetime | None]


class UrlInventoryEntry(TenantColumns, Base):
    __tablename__ = "url_inventory"
    __zone__ = Zone.tenant
    __table_args__ = (
        client_fk(),
        Index("ix_url_inventory_website_id_template_key", "website_id", "template_key"),
    )

    website_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("websites.id", ondelete="CASCADE"), primary_key=True
    )
    url_hash: Mapped[str] = mapped_column(primary_key=True)
    url: Mapped[str]
    template_key: Mapped[str | None]
    page_type_guess: Mapped[str | None]
    first_seen: Mapped[datetime]
    last_seen: Mapped[datetime]
    sitemap_lastmod: Mapped[datetime | None]


class CrawledPage(TenantColumns, Base):
    __tablename__ = "crawled_pages"
    __zone__ = Zone.tenant
    __table_args__ = (
        client_fk(),
        UniqueConstraint("website_id", "url_hash"),
        check_in("source_kind", SOURCE_KINDS),
        CheckConstraint(f"char_length(text) <= {STORED_TEXT_CHAR_CAP}", name="text_length"),
        Index("ix_crawled_pages_website_id_page_type", "website_id", "page_type"),
        Index("ix_crawled_pages_content_hash", "content_hash"),
        Index("ix_crawled_pages_fetched_at", "fetched_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    website_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("websites.id", ondelete="CASCADE"))
    url_hash: Mapped[str]
    url: Mapped[str]
    canonical_url: Mapped[str | None]
    final_url: Mapped[str | None]
    status_code: Mapped[int | None] = mapped_column(SmallInteger)
    outcome: Mapped[str]
    content_type: Mapped[str | None]
    lang: Mapped[str | None]
    title: Mapped[str | None]
    meta_description: Mapped[str | None]
    h1: Mapped[str | None]
    headings: Mapped[list[Any]] = jsonb_array()
    text: Mapped[str | None]
    content_hash: Mapped[str | None]
    simhash: Mapped[int | None] = mapped_column(BigInteger)
    json_ld: Mapped[list[Any]] = jsonb_array()
    ctas: Mapped[list[Any]] = jsonb_array()
    prices: Mapped[list[Any]] = jsonb_array()
    links_internal: Mapped[list[Any]] = jsonb_array()
    youtube_refs: Mapped[list[Any]] = jsonb_array()
    page_type: Mapped[str | None]
    template_key: Mapped[str | None]
    fetch_mode: Mapped[str]
    source_kind: Mapped[str]
    source_url: Mapped[str]
    snapshot_at: Mapped[datetime | None]
    etag: Mapped[str | None]
    last_modified: Mapped[datetime | None]
    sitemap_lastmod: Mapped[datetime | None]
    injection_flags: Mapped[int] = mapped_column(server_default=sql_text("0"))
    truncated: Mapped[bool] = mapped_column(server_default=sql_text("false"))
    low_trust: Mapped[bool] = mapped_column(server_default=sql_text("false"))
    fetched_at: Mapped[datetime]
    last_crawl_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("crawl_runs.id", ondelete="SET NULL")
    )


class PageBlob(TenantColumns, Base):
    __tablename__ = "page_blobs"
    __zone__ = Zone.tenant
    __table_args__ = (
        client_fk(),
        Index("ix_page_blobs_expires_at", "expires_at"),
    )

    page_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("crawled_pages.id", ondelete="CASCADE"), primary_key=True
    )
    raw_html_gz: Mapped[bytes]
    expires_at: Mapped[datetime]


class PageFacts(TenantColumns, Base):
    __tablename__ = "page_facts"
    __zone__ = Zone.tenant
    __table_args__ = (
        client_fk(),
        UniqueConstraint("client_id", "content_hash", "extractor_version", "prompt_version", "model"),
        check_in("source_kind", SOURCE_KINDS),
        Index("ix_page_facts_page_id", "page_id"),
        Index(
            "ix_page_facts_superseded_at",
            "superseded_at",
            postgresql_where=sql_text("superseded_at IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    page_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crawled_pages.id", ondelete="CASCADE"))
    content_hash: Mapped[str]
    extractor_version: Mapped[str]
    prompt_version: Mapped[str]
    model: Mapped[str]
    facts: Mapped[list[Any]] = jsonb_array()
    facts_schema_version: Mapped[int] = schema_version_column()
    verified_count: Mapped[int] = mapped_column(server_default=sql_text("0"))
    dropped_count: Mapped[int] = mapped_column(server_default=sql_text("0"))
    source_kind: Mapped[str]
    source_url: Mapped[str]
    snapshot_at: Mapped[datetime | None]
    fetched_at: Mapped[datetime]
    created_at: Mapped[datetime] = created_at_column()
    superseded_at: Mapped[datetime | None]


class BusinessProfile(TenantColumns, Base):
    __tablename__ = "business_profiles"
    __zone__ = Zone.tenant
    __table_args__ = (
        client_fk(),
        UniqueConstraint("client_id", "version"),
        check_in("status", PROFILE_STATUSES),
        Index(
            "ix_business_profiles_client_id_confirmed",
            "client_id",
            postgresql_where=sql_text("status = 'confirmed'"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    version: Mapped[int]
    parent_version: Mapped[int | None]
    crawl_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("crawl_runs.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(server_default=sql_text("'draft'"))
    created_by: Mapped[str]
    profile: Mapped[dict[str, Any]]
    profile_schema_version: Mapped[int] = schema_version_column()
    coverage: Mapped[dict[str, Any]] = jsonb_object()
    quality: Mapped[dict[str, Any]] = jsonb_object()
    llm_usage: Mapped[dict[str, Any]] = jsonb_object()
    prompt_versions: Mapped[dict[str, Any]] = jsonb_object()
    generated_at: Mapped[datetime]
    created_at: Mapped[datetime] = created_at_column()


class ProfileEdit(TenantColumns, Base):
    __tablename__ = "profile_edits"
    __zone__ = Zone.tenant
    __table_args__ = (
        client_fk(),
        Index("ix_profile_edits_client_id_created_at", "client_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    base_version: Mapped[int]
    result_version: Mapped[int | None]
    ops: Mapped[list[Any]] = jsonb_array()
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = created_at_column()


class ProfileDiff(TenantColumns, Base):
    __tablename__ = "profile_diffs"
    __zone__ = Zone.tenant
    __table_args__ = (
        client_fk(),
        Index("ix_profile_diffs_client_id_created_at", "client_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    from_version: Mapped[int | None]
    to_version: Mapped[int]
    diff: Mapped[dict[str, Any]] = jsonb_object()
    significance: Mapped[str]
    created_at: Mapped[datetime] = created_at_column()


class ProfileTombstone(TenantColumns, Base):
    __tablename__ = "profile_tombstones"
    __zone__ = Zone.tenant
    __table_args__ = (
        client_fk(),
        PrimaryKeyConstraint("client_id", "collection", "item_key"),
    )

    collection: Mapped[str]
    item_key: Mapped[str]
    text: Mapped[str]
    embedding: Mapped[bytes | None]
    embedding_model: Mapped[str | None]
    created_at: Mapped[datetime] = created_at_column()


class Keyword(TenantColumns, Base):
    __tablename__ = "keywords"
    __zone__ = Zone.tenant
    __table_args__ = (
        client_fk(),
        UniqueConstraint("client_id", "normalized_text", "language"),
        check_in("status", ITEM_STATUSES),
        CheckConstraint("priority BETWEEN 1 AND 3", name="priority"),
        Index("ix_keywords_client_id_status", "client_id", "status"),
        Index(
            "ix_keywords_api_data_observed_at",
            "api_data_observed_at",
            postgresql_where=sql_text("api_data_observed_at IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    text: Mapped[str]
    normalized_text: Mapped[str]
    language: Mapped[str]
    script: Mapped[str | None]
    kind: Mapped[str]
    tier: Mapped[str | None]
    source: Mapped[str]
    status: Mapped[str] = mapped_column(server_default=sql_text("'proposed'"))
    priority: Mapped[int] = mapped_column(SmallInteger, server_default=sql_text("2"))
    weight: Mapped[float] = mapped_column(REAL, server_default=sql_text("1.0"))
    offering_ids: Mapped[list[str]] = text_array()
    meta: Mapped[dict[str, Any]] = jsonb_object()
    embedding: Mapped[bytes | None]
    embedding_model: Mapped[str | None]
    uses_authorized_data: Mapped[bool] = mapped_column(server_default=sql_text("false"))
    api_data_observed_at: Mapped[datetime | None]
    introduced_in_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("business_profiles.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class BuyerQuestion(TenantColumns, Base):
    __tablename__ = "buyer_questions"
    __zone__ = Zone.tenant
    __table_args__ = (
        client_fk(),
        UniqueConstraint("client_id", "normalized_text", "language"),
        check_in("funnel_stage", FUNNEL_STAGES),
        check_in("source", BUYER_QUESTION_SOURCES),
        check_in("status", ITEM_STATUSES),
        CheckConstraint("priority BETWEEN 1 AND 3", name="priority"),
        Index("ix_buyer_questions_client_id_status", "client_id", "status"),
        Index(
            "ix_buyer_questions_ai_citations_observed_at",
            "ai_citations_observed_at",
            postgresql_where=sql_text("ai_citations_observed_at IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    text: Mapped[str]
    normalized_text: Mapped[str]
    language: Mapped[str]
    variants: Mapped[list[Any]] = jsonb_array()
    funnel_stage: Mapped[str]
    intent: Mapped[str]
    offering_ids: Mapped[list[str]] = text_array()
    conversion_action_id: Mapped[str | None]
    geo_modifier: Mapped[str | None]
    source: Mapped[str]
    source_ref: Mapped[str | None]
    status: Mapped[str] = mapped_column(server_default=sql_text("'proposed'"))
    priority: Mapped[int] = mapped_column(SmallInteger, server_default=sql_text("2"))
    ai_cited_video_ids: Mapped[list[str] | None]
    ai_citations_observed_at: Mapped[datetime | None]
    meta: Mapped[dict[str, Any]] = jsonb_object()
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()
