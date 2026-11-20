import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    REAL,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    PrimaryKeyConstraint,
    SmallInteger,
    UniqueConstraint,
    func,
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
    uuid_pk,
)
from app.models.website import FUNNEL_STAGES

TOPIC_KINDS = ("keyword", "offering", "buyer_question", "news_entity")
TOPIC_REF_STATUSES = ("active", "disabled")
MATCH_DECISIONS = ("accepted", "rejected", "unadjudicated")
MATCH_METHODS = ("lexical", "semantic", "lexical+semantic", "llm_adjudicated", "implied", "override")
MATCH_CONFIDENCES = ("high", "medium", "low")


class TopicUniverse(TenantColumns, Base):
    __tablename__ = "topic_universes"
    __zone__ = Zone.tenant
    __table_args__ = (
        client_fk(),
        PrimaryKeyConstraint("client_id", "profile_version"),
        UniqueConstraint("universe_hash"),
    )

    profile_version: Mapped[int]
    universe_hash: Mapped[str]
    engine_version: Mapped[str]
    embedder_model_ids: Mapped[dict[str, Any]] = jsonb_object()
    stats: Mapped[dict[str, Any]] = jsonb_object()
    warnings: Mapped[list[str]] = text_array()
    built_at: Mapped[datetime]


class TopicRef(TenantColumns, Base):
    __tablename__ = "topic_refs"
    __zone__ = Zone.tenant
    __table_args__ = (
        client_fk(),
        PrimaryKeyConstraint("client_id", "profile_version", "id"),
        ForeignKeyConstraint(
            ["client_id", "profile_version"],
            ["topic_universes.client_id", "topic_universes.profile_version"],
            ondelete="CASCADE",
        ),
        check_in("kind", TOPIC_KINDS),
        check_in("funnel_stage", FUNNEL_STAGES, nullable=True),
        check_in("status", TOPIC_REF_STATUSES),
        Index("ix_topic_refs_client_id_id", "client_id", "id"),
        Index("ix_topic_refs_expires_at", "expires_at", postgresql_where=sql_text("expires_at IS NOT NULL")),
    )

    profile_version: Mapped[int]
    id: Mapped[str]
    kind: Mapped[str]
    source_item_id: Mapped[str]
    label: Mapped[str]
    definition: Mapped[str | None]
    parent_ids: Mapped[list[str]] = text_array()
    offering_ids: Mapped[list[str]] = text_array()
    funnel_stage: Mapped[str | None]
    facets: Mapped[list[str]] = text_array()
    core_entity_terms: Mapped[list[str]] = text_array()
    locality_terms: Mapped[list[str]] = text_array()
    negative_contexts: Mapped[list[str]] = text_array()
    weight: Mapped[float] = mapped_column(REAL, server_default=sql_text("1.0"))
    status: Mapped[str] = mapped_column(server_default=sql_text("'active'"))
    expires_at: Mapped[datetime | None]
    fingerprint: Mapped[str]


class TopicAlias(TenantColumns, Base):
    __tablename__ = "topic_aliases"
    __zone__ = Zone.tenant
    __table_args__ = (
        client_fk(),
        ForeignKeyConstraint(
            ["client_id", "profile_version", "topic_ref_id"],
            ["topic_refs.client_id", "topic_refs.profile_version", "topic_refs.id"],
            ondelete="CASCADE",
        ),
        Index("ix_topic_aliases_client_id_topic_ref_id", "client_id", "topic_ref_id"),
        Index("ix_topic_aliases_client_id_norm_key", "client_id", "norm_key"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    topic_ref_id: Mapped[str]
    profile_version: Mapped[int]
    text: Mapped[str]
    norm_key: Mapped[str]
    phonetic_key: Mapped[str | None]
    lang: Mapped[str | None]
    script: Mapped[str]
    kind: Mapped[str]
    origin: Mapped[str]
    status: Mapped[str]
    anchor_text: Mapped[str]
    match_mode: Mapped[str]
    weight: Mapped[float] = mapped_column(REAL)
    ambiguous: Mapped[bool] = mapped_column(server_default=sql_text("false"))
    rejected_reason: Mapped[str | None]
    decided_by: Mapped[str | None]


class RefEmbedding(TenantColumns, Base):
    __tablename__ = "ref_embeddings"
    __zone__ = Zone.tenant
    __table_args__ = (
        client_fk(),
        PrimaryKeyConstraint("model_id", "ref_fingerprint", "client_id"),
    )

    model_id: Mapped[str]
    ref_fingerprint: Mapped[str]
    vector: Mapped[bytes]
    bg_mu: Mapped[float | None] = mapped_column(REAL)
    bg_q95: Mapped[float | None] = mapped_column(REAL)
    bg_q99: Mapped[float | None] = mapped_column(REAL)
    background_version: Mapped[str | None]


class ChannelBoilerplate(Base):
    __tablename__ = "channel_boilerplate"
    __zone__ = Zone.shared
    __table_args__ = (Index("ix_channel_boilerplate_computed_at", "computed_at"),)

    channel_id: Mapped[str] = mapped_column(
        ForeignKey("yt_channels.channel_id", ondelete="CASCADE"), primary_key=True
    )
    boilerplate_version: Mapped[str]
    line_keys: Mapped[list[Any]] = jsonb_array()
    hashtags: Mapped[list[str]] = text_array()
    tags: Mapped[list[str]] = text_array()
    sample_size: Mapped[int]
    computed_at: Mapped[datetime]


class VideoPreparedText(Base):
    __tablename__ = "video_prepared_text"
    __zone__ = Zone.shared
    __table_args__ = (
        Index("ix_video_prepared_text_prepared_hash", "prepared_hash"),
        Index("ix_video_prepared_text_prepared_at", "prepared_at"),
    )

    video_id: Mapped[str] = mapped_column(
        ForeignKey("yt_videos.video_id", ondelete="CASCADE"), primary_key=True
    )
    content_hash: Mapped[str]
    prepared_hash: Mapped[str]
    boilerplate_version: Mapped[str | None]
    preparer_version: Mapped[str]
    fields: Mapped[dict[str, Any]] = jsonb_object()
    removed_spans: Mapped[list[Any]] = jsonb_array()
    script_profile: Mapped[dict[str, Any]] = jsonb_object()
    code_mixed: Mapped[bool] = mapped_column(server_default=sql_text("false"))
    lang_hint: Mapped[str | None]
    quality_flags: Mapped[list[str]] = text_array()
    prepared_at: Mapped[datetime]


class TextEmbedding(Base):
    __tablename__ = "text_embeddings"
    __zone__ = Zone.shared
    __table_args__ = (
        PrimaryKeyConstraint("model_id", "text_hash", "role"),
        Index("ix_text_embeddings_source_video_id", "source_video_id"),
        Index("ix_text_embeddings_created_at", "created_at"),
    )

    model_id: Mapped[str]
    text_hash: Mapped[str]
    role: Mapped[str]
    dim: Mapped[int] = mapped_column(SmallInteger)
    vector: Mapped[bytes]
    source_video_id: Mapped[str | None] = mapped_column(ForeignKey("yt_videos.video_id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = created_at_column()


class TopicMatch(TenantColumns, Base):
    __tablename__ = "topic_matches"
    __zone__ = Zone.tenant
    __table_args__ = (
        client_fk(),
        UniqueConstraint("client_id", "video_id", "topic_ref_id", "universe_hash"),
        check_in("topic_kind", TOPIC_KINDS),
        check_in("decision", MATCH_DECISIONS),
        check_in("method", MATCH_METHODS),
        check_in("confidence", MATCH_CONFIDENCES),
        Index(
            "ix_topic_matches_client_id_topic_ref_id_decision_confidence",
            "client_id",
            "topic_ref_id",
            "decision",
            "confidence",
        ),
        Index("ix_topic_matches_client_id_video_id", "client_id", "video_id"),
        Index("ix_topic_matches_video_id", "video_id"),
        Index("ix_topic_matches_expires_at", "expires_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    video_id: Mapped[str]
    topic_ref_id: Mapped[str]
    topic_kind: Mapped[str]
    profile_version: Mapped[int]
    universe_hash: Mapped[str]
    prepared_hash: Mapped[str]
    engine_version: Mapped[str]
    decision: Mapped[str]
    lexical_score: Mapped[float] = mapped_column(REAL)
    semantic_score: Mapped[float | None] = mapped_column(REAL)
    combined_score: Mapped[float] = mapped_column(REAL)
    method: Mapped[str]
    confidence: Mapped[str]
    is_primary: Mapped[bool] = mapped_column(server_default=sql_text("false"))
    rank: Mapped[int] = mapped_column(SmallInteger)
    implied_from: Mapped[str | None]
    review_reasons: Mapped[list[str]] = text_array()
    degraded: Mapped[list[str]] = text_array()
    evidence: Mapped[list[Any]] = jsonb_array()
    evidence_schema_version: Mapped[int] = schema_version_column()
    trace: Mapped[dict[str, Any]] = jsonb_object()
    explanation: Mapped[str]
    matched_at: Mapped[datetime]
    deleted_at: Mapped[datetime | None]
    api_data_observed_at: Mapped[datetime]
    expires_at: Mapped[datetime]


class AnswerDirectness(TenantColumns, Base):
    __tablename__ = "answer_directness"
    __zone__ = Zone.tenant
    __table_args__ = (
        client_fk(),
        PrimaryKeyConstraint("client_id", "video_id", "bq_ref_id"),
        Index("ix_answer_directness_client_id_bq_ref_id_level", "client_id", "bq_ref_id", "level"),
        Index("ix_answer_directness_expires_at", "expires_at"),
    )

    video_id: Mapped[str]
    bq_ref_id: Mapped[str]
    universe_hash: Mapped[str]
    prepared_hash: Mapped[str]
    ref_fingerprint: Mapped[str]
    directness_version: Mapped[str]
    score: Mapped[float] = mapped_column(REAL)
    level: Mapped[str]
    components: Mapped[dict[str, Any]] = jsonb_object()
    facets_covered: Mapped[list[str]] = text_array()
    facets_missing: Mapped[list[str]] = text_array()
    method: Mapped[str]
    explanation: Mapped[str]
    computed_at: Mapped[datetime]
    api_data_observed_at: Mapped[datetime]
    expires_at: Mapped[datetime]


class LlmAdjudication(TenantColumns, Base):
    __tablename__ = "llm_adjudications"
    __zone__ = Zone.tenant
    __table_args__ = (
        client_fk(),
        Index("ix_llm_adjudications_client_id", "client_id"),
        Index("ix_llm_adjudications_video_ids", "video_ids", postgresql_using="gin"),
        Index("ix_llm_adjudications_expires_at", "expires_at"),
    )

    cache_key: Mapped[str] = mapped_column(primary_key=True)
    model: Mapped[str]
    prompt_version: Mapped[str]
    request_id: Mapped[str | None]
    video_ids: Mapped[list[str]] = text_array()
    verdict: Mapped[dict[str, Any]] = jsonb_object()
    verdict_schema_version: Mapped[int] = schema_version_column()
    tokens_in: Mapped[int] = mapped_column(server_default=sql_text("0"))
    tokens_out: Mapped[int] = mapped_column(server_default=sql_text("0"))
    latency_ms: Mapped[int | None]
    created_at: Mapped[datetime] = created_at_column()
    api_data_observed_at: Mapped[datetime]
    expires_at: Mapped[datetime]


class TopicMatchOverride(TenantColumns, Base):
    __tablename__ = "topic_match_overrides"
    __zone__ = Zone.tenant
    __table_args__ = (
        client_fk(),
        UniqueConstraint("client_id", "video_id", "topic_ref_id"),
        Index("ix_topic_match_overrides_ids_verified_at", "ids_verified_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    video_id: Mapped[str]
    topic_ref_id: Mapped[str]
    verdict: Mapped[str]
    reassigned_to_ref_id: Mapped[str | None]
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reason: Mapped[str | None]
    content_hash_at_review: Mapped[str]
    stale: Mapped[bool] = mapped_column(server_default=sql_text("false"))
    created_at: Mapped[datetime] = created_at_column()
    ids_verified_at: Mapped[datetime] = mapped_column(server_default=func.now())


class TopicReviewItem(TenantColumns, Base):
    __tablename__ = "topic_review_items"
    __zone__ = Zone.tenant
    __table_args__ = (
        client_fk(),
        Index(
            "ix_topic_review_items_client_id_status_priority",
            "client_id",
            "status",
            sql_text("priority DESC"),
        ),
        Index("ix_topic_review_items_video_id", "video_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    video_id: Mapped[str]
    topic_ref_id: Mapped[str]
    reasons: Mapped[list[str]] = text_array()
    priority: Mapped[float] = mapped_column(REAL)
    status: Mapped[str] = mapped_column(server_default=sql_text("'open'"))
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    resolved_at: Mapped[datetime | None]
    resolution: Mapped[str | None]


class TopicEvalLabel(Base):
    __tablename__ = "topic_eval_labels"
    __zone__ = Zone.evaluation
    __table_args__ = (
        Index("ix_topic_eval_labels_label_set_version_split", "label_set_version", "split"),
        Index("ix_topic_eval_labels_ids_verified_at", "ids_verified_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    label_set_version: Mapped[str]
    client_id: Mapped[uuid.UUID | None]
    video_id: Mapped[str]
    topic_ref_id: Mapped[str]
    label: Mapped[str]
    directness_level: Mapped[str | None]
    annotator_id: Mapped[uuid.UUID | None]
    source: Mapped[str]
    split: Mapped[str]
    created_at: Mapped[datetime] = created_at_column()
    ids_verified_at: Mapped[datetime] = mapped_column(server_default=func.now())
