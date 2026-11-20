"""topics, recommendations, news, refresh, llm, evaluation, ledgers

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "alert_event",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("rule_id", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("value", sa.Double(), nullable=False),
        sa.Column("threshold", sa.Double(), nullable=False),
        sa.Column("fired_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("resolved_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alert_event")),
    )
    op.create_index(op.f("ix_alert_event_fired_at"), "alert_event", ["fired_at"], unique=False)
    op.create_index("ix_alert_event_rule_id_fired_at", "alert_event", ["rule_id", "fired_at"], unique=False)
    op.create_index(
        "ix_alert_event_rule_id_open",
        "alert_event",
        ["rule_id"],
        unique=False,
        postgresql_where=sa.text("resolved_at IS NULL"),
    )
    op.create_table(
        "eval_config",
        sa.Column("eval_id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("config_schema_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("config_hash", sa.Text(), nullable=False),
        sa.Column("code_version", sa.Text(), nullable=False),
        sa.Column("seed", sa.BigInteger(), nullable=False),
        sa.Column(
            "registered_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("status", sa.Text(), server_default=sa.text("'registered'"), nullable=False),
        sa.Column("started_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("finished_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "usd_spent", sa.Numeric(precision=12, scale=6), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("units_spent", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.PrimaryKeyConstraint("eval_id", name=op.f("pk_eval_config")),
    )
    op.create_index(op.f("ix_eval_config_status"), "eval_config", ["status"], unique=False)
    op.create_table(
        "eval_freeze",
        sa.Column("freeze_id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=True),
        sa.Column("client_id", sa.UUID(), nullable=True),
        sa.Column("as_of", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "channel_ids", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False
        ),
        sa.Column("competitor_set_version", sa.Integer(), nullable=False),
        sa.Column("topic_ref_version", sa.Integer(), nullable=False),
        sa.Column("business_profile_version", sa.Integer(), nullable=False),
        sa.Column("gate_config_version", sa.Text(), nullable=False),
        sa.Column("analytics_config_hash", sa.Text(), nullable=False),
        sa.Column(
            "record_counts",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("bundle_uri", sa.Text(), nullable=True),
        sa.Column("pit_level", sa.Text(), nullable=False),
        sa.Column("oldest_api_fetched_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("expires_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'active'"), nullable=False),
        sa.Column("purged_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("freeze_id", name=op.f("pk_eval_freeze")),
    )
    op.create_index("ix_eval_freeze_client_id_as_of", "eval_freeze", ["client_id", "as_of"], unique=False)
    op.create_index(op.f("ix_eval_freeze_expires_at"), "eval_freeze", ["expires_at"], unique=False)
    op.create_table(
        "gate_tuning_proposal",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("batch_id", sa.UUID(), nullable=False),
        sa.Column("feature", sa.Text(), nullable=False),
        sa.Column("rho", sa.REAL(), nullable=False),
        sa.Column("current_threshold", sa.Double(), nullable=True),
        sa.Column("proposed_threshold", sa.Double(), nullable=True),
        sa.Column("retained_share", sa.REAL(), nullable=False),
        sa.Column("retained_mean_rating", sa.REAL(), nullable=False),
        sa.Column(
            "config_diff",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'proposed'"), nullable=False),
        sa.Column("decided_by", sa.UUID(), nullable=True),
        sa.Column("decided_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_gate_tuning_proposal")),
    )
    op.create_index(
        op.f("ix_gate_tuning_proposal_batch_id"), "gate_tuning_proposal", ["batch_id"], unique=False
    )
    op.create_table(
        "label_task",
        sa.Column("task_id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("batch_id", sa.UUID(), nullable=False),
        sa.Column("video_id", sa.Text(), nullable=True),
        sa.Column("channel_id", sa.Text(), nullable=True),
        sa.Column("stratum", sa.Text(), nullable=False),
        sa.Column("inclusion_prob", sa.Double(), nullable=False),
        sa.Column("detector_ratio", sa.REAL(), nullable=True),
        sa.Column("detector_label", sa.Boolean(), nullable=False),
        sa.Column("detector_version", sa.Text(), nullable=False),
        sa.Column("observed_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("expires_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("task_id", name=op.f("pk_label_task")),
    )
    op.create_index(op.f("ix_label_task_batch_id"), "label_task", ["batch_id"], unique=False)
    op.create_index(op.f("ix_label_task_expires_at"), "label_task", ["expires_at"], unique=False)
    op.create_table(
        "link_check_cache",
        sa.Column("url_normalised", sa.Text(), nullable=False),
        sa.Column("status_class", sa.Text(), nullable=False),
        sa.Column("http_status", sa.SmallInteger(), nullable=True),
        sa.Column("final_url", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("checked_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("next_check_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("url_normalised", name=op.f("pk_link_check_cache")),
    )
    op.create_index(op.f("ix_link_check_cache_checked_at"), "link_check_cache", ["checked_at"], unique=False)
    op.create_index(
        op.f("ix_link_check_cache_next_check_at"), "link_check_cache", ["next_check_at"], unique=False
    )
    op.create_table(
        "llm_debug_payloads",
        sa.Column("call_id", sa.UUID(), nullable=False),
        sa.Column("request_redacted", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("response_text_redacted", sa.Text(), nullable=True),
        sa.Column("expires_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("call_id", name=op.f("pk_llm_debug_payloads")),
    )
    op.create_index(
        op.f("ix_llm_debug_payloads_expires_at"), "llm_debug_payloads", ["expires_at"], unique=False
    )
    op.create_table(
        "llm_result_cache",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("task", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("output", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("expires_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_llm_result_cache")),
    )
    op.create_index(op.f("ix_llm_result_cache_expires_at"), "llm_result_cache", ["expires_at"], unique=False)
    op.create_index(op.f("ix_llm_result_cache_task"), "llm_result_cache", ["task"], unique=False)
    op.create_table(
        "news_queries",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("source_key", sa.Text(), nullable=False),
        sa.Column("normalized_query", sa.Text(), nullable=False),
        sa.Column(
            "params",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("query_hash", sa.Text(), nullable=False),
        sa.Column("cadence_minutes", sa.Integer(), nullable=False),
        sa.Column("next_run_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("last_run_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_status", sa.Text(), nullable=True),
        sa.Column("subscriber_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("orphaned_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_news_queries")),
        sa.UniqueConstraint("query_hash", name=op.f("uq_news_queries_query_hash")),
    )
    op.create_index(
        "ix_news_queries_next_run_at",
        "news_queries",
        ["next_run_at"],
        unique=False,
        postgresql_where=sa.text("subscriber_count > 0"),
    )
    op.create_index(
        "ix_news_queries_orphaned_at",
        "news_queries",
        ["orphaned_at"],
        unique=False,
        postgresql_where=sa.text("subscriber_count = 0"),
    )
    op.create_table(
        "news_stories",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("first_published_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("last_published_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("representative_item_id", sa.UUID(), nullable=True),
        sa.Column("outlet_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("best_tier", sa.Text(), nullable=False),
        sa.Column(
            "version_tokens",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("centroid", postgresql.BYTEA(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_news_stories")),
    )
    op.create_index(
        "ix_news_stories_first_published_at",
        "news_stories",
        [sa.literal_column("first_published_at DESC")],
        unique=False,
    )
    op.create_index(
        op.f("ix_news_stories_last_published_at"), "news_stories", ["last_published_at"], unique=False
    )
    op.create_index(op.f("ix_news_stories_status"), "news_stories", ["status"], unique=False)
    op.create_table(
        "ops_daily_rollup",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=True),
        sa.Column("client_id", sa.UUID(), nullable=True),
        sa.Column(
            "metrics",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("metrics_schema_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "updated_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ops_daily_rollup")),
    )
    op.create_index(
        "uq_ops_daily_rollup_day_client_id",
        "ops_daily_rollup",
        ["day", "client_id"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )
    op.create_table(
        "outlier_eval_result",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("batch_id", sa.UUID(), nullable=False),
        sa.Column("detector_version", sa.Text(), nullable=False),
        sa.Column(
            "metrics",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("metrics_schema_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "curve",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outlier_eval_result")),
    )
    op.create_index(
        op.f("ix_outlier_eval_result_batch_id"), "outlier_eval_result", ["batch_id"], unique=False
    )
    op.create_table(
        "provider_usage_ledger",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("request_id", sa.UUID(), nullable=False),
        sa.Column("occurred_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("provider_kind", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=True),
        sa.Column("stage", sa.Text(), nullable=True),
        sa.Column("tier", sa.Text(), nullable=True),
        sa.Column("prompt_id", sa.Text(), nullable=True),
        sa.Column("prompt_version", sa.Text(), nullable=True),
        sa.Column("schema_name", sa.Text(), nullable=True),
        sa.Column("model_requested", sa.Text(), nullable=True),
        sa.Column("model_resolved", sa.Text(), nullable=True),
        sa.Column("route_index", sa.SmallInteger(), nullable=True),
        sa.Column("attempt_no", sa.SmallInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column("attempt_kind", sa.Text(), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("error_kind", sa.Text(), nullable=True),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("stop_reason", sa.Text(), nullable=True),
        sa.Column("refusal_category", sa.Text(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=True),
        sa.Column("cache_write_tokens", sa.Integer(), nullable=True),
        sa.Column("thinking_tokens", sa.Integer(), nullable=True),
        sa.Column("credits", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("pages", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column("price_missing", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("provider_request_id", sa.Text(), nullable=True),
        sa.Column("input_hash", sa.Text(), nullable=True),
        sa.Column("output_hash", sa.Text(), nullable=True),
        sa.Column("cache_hit", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("degraded_route", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=True),
        sa.Column("client_id", sa.UUID(), nullable=True),
        sa.Column("run_id", sa.UUID(), nullable=True),
        sa.CheckConstraint(
            "provider_kind IN ('llm', 'tavily', 'crawl', 'embedding', 'freshness')",
            name=op.f("ck_provider_usage_ledger_provider_kind"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_provider_usage_ledger")),
        sa.UniqueConstraint("request_id", name=op.f("uq_provider_usage_ledger_request_id")),
    )
    op.create_index(
        "ix_provider_usage_ledger_client_id_occurred_at",
        "provider_usage_ledger",
        ["client_id", "occurred_at"],
        unique=False,
    )
    op.create_index(
        "ix_provider_usage_ledger_occurred_at_brin",
        "provider_usage_ledger",
        ["occurred_at"],
        unique=False,
        postgresql_using="brin",
    )
    op.create_index(
        "ix_provider_usage_ledger_operation_occurred_at",
        "provider_usage_ledger",
        ["operation", "occurred_at"],
        unique=False,
    )
    op.create_index(
        "ix_provider_usage_ledger_provider_model_resolved_occurred_at",
        "provider_usage_ledger",
        ["provider", "model_resolved", "occurred_at"],
        unique=False,
    )
    op.create_index("ix_provider_usage_ledger_run_id", "provider_usage_ledger", ["run_id"], unique=False)
    op.create_table(
        "quota_cache_hits_daily",
        sa.Column("pt_day", sa.Date(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.Column("data_kind", sa.Text(), nullable=False),
        sa.Column("hits", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.PrimaryKeyConstraint("pt_day", "client_id", "data_kind", name=op.f("pk_quota_cache_hits_daily")),
    )
    op.create_table(
        "review_item",
        sa.Column("item_id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("batch_id", sa.UUID(), nullable=False),
        sa.Column("recommendation_id", sa.UUID(), nullable=True),
        sa.Column("workspace_id", sa.UUID(), nullable=True),
        sa.Column("client_id", sa.UUID(), nullable=True),
        sa.Column("card_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("card_snapshot_schema_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("is_decoy", sa.Boolean(), nullable=False),
        sa.Column(
            "gate_features",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("display_seed", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("item_id", name=op.f("pk_review_item")),
    )
    op.create_index(op.f("ix_review_item_batch_id"), "review_item", ["batch_id"], unique=False)
    op.create_index(op.f("ix_review_item_expires_at"), "review_item", ["expires_at"], unique=False)
    op.create_table(
        "source_tiers",
        sa.Column("domain", sa.Text(), nullable=False),
        sa.Column("tier", sa.Text(), nullable=False),
        sa.Column(
            "packs", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("updated_by", sa.Text(), nullable=False),
        sa.Column(
            "updated_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("domain", name=op.f("pk_source_tiers")),
    )
    op.create_table(
        "topic_eval_labels",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("label_set_version", sa.Text(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=True),
        sa.Column("video_id", sa.Text(), nullable=False),
        sa.Column("topic_ref_id", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("directness_level", sa.Text(), nullable=True),
        sa.Column("annotator_id", sa.UUID(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("split", sa.Text(), nullable=False),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "ids_verified_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_topic_eval_labels")),
    )
    op.create_index(
        "ix_topic_eval_labels_ids_verified_at", "topic_eval_labels", ["ids_verified_at"], unique=False
    )
    op.create_index(
        "ix_topic_eval_labels_label_set_version_split",
        "topic_eval_labels",
        ["label_set_version", "split"],
        unique=False,
    )
    op.create_table(
        "yt_quota_ledger",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("request_id", sa.UUID(), nullable=False),
        sa.Column("occurred_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("pt_day", sa.Date(), nullable=False),
        sa.Column("project", sa.Text(), server_default=sa.text("'default'"), nullable=False),
        sa.Column("bucket", sa.Text(), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("units", sa.Integer(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("priority", sa.Text(), nullable=True),
        sa.Column("cache_outcome", sa.Text(), nullable=True),
        sa.Column("http_status", sa.SmallInteger(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("cache_key", sa.Text(), nullable=True),
        sa.Column("workspace_id", sa.UUID(), nullable=True),
        sa.Column("client_id", sa.UUID(), nullable=True),
        sa.Column("run_id", sa.UUID(), nullable=True),
        sa.CheckConstraint(
            "bucket IN ('main', 'search', 'batch_stats', 'analytics')", name=op.f("ck_yt_quota_ledger_bucket")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_yt_quota_ledger")),
        sa.UniqueConstraint("request_id", name=op.f("uq_yt_quota_ledger_request_id")),
    )
    op.create_index(
        "ix_yt_quota_ledger_client_id_pt_day", "yt_quota_ledger", ["client_id", "pt_day"], unique=False
    )
    op.create_index(
        "ix_yt_quota_ledger_occurred_at_brin",
        "yt_quota_ledger",
        ["occurred_at"],
        unique=False,
        postgresql_using="brin",
    )
    op.create_index("ix_yt_quota_ledger_pt_day_bucket", "yt_quota_ledger", ["pt_day", "bucket"], unique=False)
    op.create_index("ix_yt_quota_ledger_run_id", "yt_quota_ledger", ["run_id"], unique=False)
    op.create_table(
        "eval_outcome_video",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("eval_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=True),
        sa.Column("client_id", sa.UUID(), nullable=True),
        sa.Column("video_id", sa.Text(), nullable=False),
        sa.Column("channel_id", sa.Text(), nullable=False),
        sa.Column("published_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("views_at_age", sa.BigInteger(), nullable=True),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("baseline", sa.REAL(), nullable=True),
        sa.Column("ratio", sa.REAL(), nullable=True),
        sa.Column("is_outlier", sa.Boolean(), nullable=True),
        sa.Column(
            "matched",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("expires_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.CheckConstraint(
            "method IN ('observed', 'interpolated', 'owner_analytics', 'age_band_proxy', 'pending', 'unavailable')",
            name=op.f("ck_eval_outcome_video_method"),
        ),
        sa.ForeignKeyConstraint(
            ["eval_id"],
            ["eval_config.eval_id"],
            name=op.f("fk_eval_outcome_video_eval_id_eval_config"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_eval_outcome_video")),
    )
    op.create_index(op.f("ix_eval_outcome_video_eval_id"), "eval_outcome_video", ["eval_id"], unique=False)
    op.create_index(
        op.f("ix_eval_outcome_video_expires_at"), "eval_outcome_video", ["expires_at"], unique=False
    )
    op.create_table(
        "eval_pattern_spec",
        sa.Column("eval_id", sa.UUID(), nullable=False),
        sa.Column("spec_id", sa.Text(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=True),
        sa.Column("client_id", sa.UUID(), nullable=True),
        sa.Column("arm", sa.Text(), nullable=False),
        sa.Column("replicate", sa.Integer(), nullable=True),
        sa.Column(
            "spec",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("spec_schema_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("specificity", sa.SmallInteger(), nullable=False),
        sa.Column("source_ref", sa.Text(), nullable=True),
        sa.Column("channel_id", sa.Text(), nullable=True),
        sa.Column("expires_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["eval_id"],
            ["eval_config.eval_id"],
            name=op.f("fk_eval_pattern_spec_eval_id_eval_config"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("eval_id", "spec_id", name=op.f("pk_eval_pattern_spec")),
    )
    op.create_index("ix_eval_pattern_spec_eval_id_arm", "eval_pattern_spec", ["eval_id", "arm"], unique=False)
    op.create_index(
        op.f("ix_eval_pattern_spec_expires_at"), "eval_pattern_spec", ["expires_at"], unique=False
    )
    op.create_table(
        "eval_result",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("eval_id", sa.UUID(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("arm", sa.Text(), nullable=False),
        sa.Column("metric", sa.Text(), nullable=False),
        sa.Column("estimate", sa.Double(), nullable=True),
        sa.Column("ci_low", sa.Double(), nullable=True),
        sa.Column("ci_high", sa.Double(), nullable=True),
        sa.Column("n_videos", sa.Integer(), nullable=False),
        sa.Column("n_channels", sa.Integer(), nullable=False),
        sa.Column("p_value", sa.Double(), nullable=True),
        sa.Column("p_adj", sa.Double(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["eval_id"],
            ["eval_config.eval_id"],
            name=op.f("fk_eval_result_eval_id_eval_config"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_eval_result")),
    )
    op.create_index("ix_eval_result_eval_id_scope", "eval_result", ["eval_id", "scope"], unique=False)
    op.create_table(
        "label",
        sa.Column("task_id", sa.UUID(), nullable=False),
        sa.Column("labeller_id", sa.UUID(), nullable=False),
        sa.Column("round", sa.SmallInteger(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column(
            "reason_codes",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("seconds_spent", sa.Integer(), nullable=True),
        sa.Column("labelled_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["task_id"], ["label_task.task_id"], name=op.f("fk_label_task_id_label_task"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("task_id", "labeller_id", "round", name=op.f("pk_label")),
    )
    op.create_table(
        "label_gold",
        sa.Column("task_id", sa.UUID(), nullable=False),
        sa.Column("gold", sa.Text(), nullable=False),
        sa.Column("method", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["label_task.task_id"],
            name=op.f("fk_label_gold_task_id_label_task"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("task_id", name=op.f("pk_label_gold")),
    )
    op.create_table(
        "news_items",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("source_key", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("url_hash", sa.Text(), nullable=False),
        sa.Column("domain", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("snippet", sa.Text(), nullable=False),
        sa.Column("language", sa.Text(), nullable=True),
        sa.Column("published_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("published_precision", sa.Text(), nullable=False),
        sa.Column("fetched_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("source_rank_score", sa.REAL(), nullable=True),
        sa.Column(
            "external_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("source_tier", sa.Text(), nullable=False),
        sa.Column(
            "flags", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False
        ),
        sa.Column("story_id", sa.UUID(), nullable=True),
        sa.Column("embedding", postgresql.BYTEA(), nullable=True),
        sa.CheckConstraint("char_length(snippet) <= 1500", name=op.f("ck_news_items_snippet_length")),
        sa.ForeignKeyConstraint(
            ["story_id"],
            ["news_stories.id"],
            name=op.f("fk_news_items_story_id_news_stories"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_news_items")),
        sa.UniqueConstraint("url_hash", name=op.f("uq_news_items_url_hash")),
    )
    op.create_index(op.f("ix_news_items_domain"), "news_items", ["domain"], unique=False)
    op.create_index(
        "ix_news_items_external_ids", "news_items", ["external_ids"], unique=False, postgresql_using="gin"
    )
    op.create_index(op.f("ix_news_items_fetched_at"), "news_items", ["fetched_at"], unique=False)
    op.create_index(
        "ix_news_items_published_at", "news_items", [sa.literal_column("published_at DESC")], unique=False
    )
    op.create_index(op.f("ix_news_items_story_id"), "news_items", ["story_id"], unique=False)
    op.create_table(
        "news_query_runs",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("query_id", sa.UUID(), nullable=False),
        sa.Column("started_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("finished_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("cost_credits", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column("item_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("provider_request_id", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["query_id"],
            ["news_queries.id"],
            name=op.f("fk_news_query_runs_query_id_news_queries"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_news_query_runs")),
    )
    op.create_index(
        "ix_news_query_runs_query_id_started_at",
        "news_query_runs",
        ["query_id", sa.literal_column("started_at DESC")],
        unique=False,
    )
    op.create_index("ix_news_query_runs_started_at", "news_query_runs", ["started_at"], unique=False)
    op.create_table(
        "review_rating",
        sa.Column("item_id", sa.UUID(), nullable=False),
        sa.Column("rater_id", sa.UUID(), nullable=False),
        sa.Column("specific", sa.SmallInteger(), nullable=False),
        sa.Column("evidenced", sa.SmallInteger(), nullable=False),
        sa.Column("relevant", sa.SmallInteger(), nullable=False),
        sa.Column("actionable", sa.SmallInteger(), nullable=False),
        sa.Column("not_obvious", sa.SmallInteger(), nullable=False),
        sa.Column("would_ship", sa.Boolean(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("rated_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.CheckConstraint("actionable BETWEEN 1 AND 5", name=op.f("ck_review_rating_actionable")),
        sa.CheckConstraint("evidenced BETWEEN 1 AND 5", name=op.f("ck_review_rating_evidenced")),
        sa.CheckConstraint("not_obvious BETWEEN 1 AND 5", name=op.f("ck_review_rating_not_obvious")),
        sa.CheckConstraint("relevant BETWEEN 1 AND 5", name=op.f("ck_review_rating_relevant")),
        sa.CheckConstraint("specific BETWEEN 1 AND 5", name=op.f("ck_review_rating_specific")),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["review_item.item_id"],
            name=op.f("fk_review_rating_item_id_review_item"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("item_id", "rater_id", name=op.f("pk_review_rating")),
    )
    op.create_table(
        "answer_directness",
        sa.Column("video_id", sa.Text(), nullable=False),
        sa.Column("bq_ref_id", sa.Text(), nullable=False),
        sa.Column("universe_hash", sa.Text(), nullable=False),
        sa.Column("prepared_hash", sa.Text(), nullable=False),
        sa.Column("ref_fingerprint", sa.Text(), nullable=False),
        sa.Column("directness_version", sa.Text(), nullable=False),
        sa.Column("score", sa.REAL(), nullable=False),
        sa.Column("level", sa.Text(), nullable=False),
        sa.Column(
            "components",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "facets_covered",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column(
            "facets_missing",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("computed_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("api_data_observed_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("expires_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name=op.f("fk_answer_directness_workspace_id_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("client_id", "video_id", "bq_ref_id", name=op.f("pk_answer_directness")),
    )
    op.create_index(
        "ix_answer_directness_client_id_bq_ref_id_level",
        "answer_directness",
        ["client_id", "bq_ref_id", "level"],
        unique=False,
    )
    op.create_index("ix_answer_directness_expires_at", "answer_directness", ["expires_at"], unique=False)
    op.create_table(
        "client_recommend_config",
        sa.Column("lens_mode", sa.Text(), server_default=sa.text("'both'"), nullable=False),
        sa.Column(
            "recommend_overrides",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "excluded_formats",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column(
            "excluded_creators",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name=op.f("fk_client_recommend_config_workspace_id_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("client_id", name=op.f("pk_client_recommend_config")),
    )
    op.create_table(
        "llm_adjudications",
        sa.Column("cache_key", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.Column(
            "video_ids", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False
        ),
        sa.Column(
            "verdict",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("verdict_schema_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("tokens_in", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("tokens_out", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("api_data_observed_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("expires_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name=op.f("fk_llm_adjudications_workspace_id_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("cache_key", name=op.f("pk_llm_adjudications")),
    )
    op.create_index("ix_llm_adjudications_client_id", "llm_adjudications", ["client_id"], unique=False)
    op.create_index("ix_llm_adjudications_expires_at", "llm_adjudications", ["expires_at"], unique=False)
    op.create_index(
        "ix_llm_adjudications_video_ids",
        "llm_adjudications",
        ["video_ids"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_table(
        "news_judgements",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("story_id", sa.UUID(), nullable=False),
        sa.Column("profile_version", sa.Integer(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("judgement", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("judgement_schema_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("grounded", sa.Boolean(), nullable=False),
        sa.Column("tokens_in", sa.Integer(), nullable=False),
        sa.Column("tokens_out", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name=op.f("fk_news_judgements_workspace_id_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_news_judgements")),
        sa.UniqueConstraint(
            "client_id",
            "story_id",
            "profile_version",
            name=op.f("uq_news_judgements_client_id_story_id_profile_version"),
        ),
    )
    op.create_index("ix_news_judgements_created_at", "news_judgements", ["created_at"], unique=False)
    op.create_table(
        "news_query_subscriptions",
        sa.Column("query_id", sa.UUID(), nullable=False),
        sa.Column("origin", sa.Text(), nullable=False),
        sa.Column("weight", sa.REAL(), nullable=False),
        sa.Column("profile_version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["query_id"],
            ["news_queries.id"],
            name=op.f("fk_news_query_subscriptions_query_id_news_queries"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name=op.f("fk_news_query_subscriptions_workspace_id_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("client_id", "query_id", name=op.f("pk_news_query_subscriptions")),
    )
    op.create_index(
        op.f("ix_news_query_subscriptions_query_id"), "news_query_subscriptions", ["query_id"], unique=False
    )
    op.create_table(
        "recommendation_outcomes",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("stable_key", sa.Text(), nullable=False),
        sa.Column("video_id", sa.Text(), nullable=True),
        sa.Column("video_verified_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("matched_by", sa.Text(), nullable=False),
        sa.Column("comparison_age_days", sa.SmallInteger(), nullable=False),
        sa.Column("predicted_low", sa.REAL(), nullable=True),
        sa.Column("predicted_high", sa.REAL(), nullable=True),
        sa.Column("observed_ratio", sa.REAL(), nullable=True),
        sa.Column("within_range", sa.Boolean(), nullable=True),
        sa.Column("became_outlier", sa.Boolean(), nullable=True),
        sa.Column("measured_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name=op.f("fk_recommendation_outcomes_workspace_id_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_recommendation_outcomes")),
    )
    op.create_index(
        "ix_recommendation_outcomes_client_id_stable_key",
        "recommendation_outcomes",
        ["client_id", "stable_key"],
        unique=False,
    )
    op.create_index(
        "ix_recommendation_outcomes_measured_at",
        "recommendation_outcomes",
        ["measured_at"],
        unique=False,
        postgresql_where=sa.text("observed_ratio IS NOT NULL"),
    )
    op.create_index(
        "ix_recommendation_outcomes_video_verified_at",
        "recommendation_outcomes",
        ["video_verified_at"],
        unique=False,
        postgresql_where=sa.text("video_id IS NOT NULL"),
    )
    op.create_table(
        "ref_embeddings",
        sa.Column("model_id", sa.Text(), nullable=False),
        sa.Column("ref_fingerprint", sa.Text(), nullable=False),
        sa.Column("vector", postgresql.BYTEA(), nullable=False),
        sa.Column("bg_mu", sa.REAL(), nullable=True),
        sa.Column("bg_q95", sa.REAL(), nullable=True),
        sa.Column("bg_q99", sa.REAL(), nullable=True),
        sa.Column("background_version", sa.Text(), nullable=True),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name=op.f("fk_ref_embeddings_workspace_id_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("model_id", "ref_fingerprint", "client_id", name=op.f("pk_ref_embeddings")),
    )
    op.create_table(
        "topic_match_overrides",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("video_id", sa.Text(), nullable=False),
        sa.Column("topic_ref_id", sa.Text(), nullable=False),
        sa.Column("verdict", sa.Text(), nullable=False),
        sa.Column("reassigned_to_ref_id", sa.Text(), nullable=True),
        sa.Column("reviewer_id", sa.UUID(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("content_hash_at_review", sa.Text(), nullable=False),
        sa.Column("stale", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "ids_verified_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["reviewer_id"],
            ["users.id"],
            name=op.f("fk_topic_match_overrides_reviewer_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name=op.f("fk_topic_match_overrides_workspace_id_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_topic_match_overrides")),
        sa.UniqueConstraint(
            "client_id",
            "video_id",
            "topic_ref_id",
            name=op.f("uq_topic_match_overrides_client_id_video_id_topic_ref_id"),
        ),
    )
    op.create_index(
        "ix_topic_match_overrides_ids_verified_at", "topic_match_overrides", ["ids_verified_at"], unique=False
    )
    op.create_table(
        "topic_matches",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("video_id", sa.Text(), nullable=False),
        sa.Column("topic_ref_id", sa.Text(), nullable=False),
        sa.Column("topic_kind", sa.Text(), nullable=False),
        sa.Column("profile_version", sa.Integer(), nullable=False),
        sa.Column("universe_hash", sa.Text(), nullable=False),
        sa.Column("prepared_hash", sa.Text(), nullable=False),
        sa.Column("engine_version", sa.Text(), nullable=False),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("lexical_score", sa.REAL(), nullable=False),
        sa.Column("semantic_score", sa.REAL(), nullable=True),
        sa.Column("combined_score", sa.REAL(), nullable=False),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Text(), nullable=False),
        sa.Column("is_primary", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("rank", sa.SmallInteger(), nullable=False),
        sa.Column("implied_from", sa.Text(), nullable=True),
        sa.Column(
            "review_reasons",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column(
            "degraded", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False
        ),
        sa.Column(
            "evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("evidence_schema_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "trace",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("matched_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("deleted_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("api_data_observed_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("expires_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "confidence IN ('high', 'medium', 'low')", name=op.f("ck_topic_matches_confidence")
        ),
        sa.CheckConstraint(
            "decision IN ('accepted', 'rejected', 'unadjudicated')", name=op.f("ck_topic_matches_decision")
        ),
        sa.CheckConstraint(
            "method IN ('lexical', 'semantic', 'lexical+semantic', 'llm_adjudicated', 'implied', 'override')",
            name=op.f("ck_topic_matches_method"),
        ),
        sa.CheckConstraint(
            "topic_kind IN ('keyword', 'offering', 'buyer_question', 'news_entity')",
            name=op.f("ck_topic_matches_topic_kind"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name=op.f("fk_topic_matches_workspace_id_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_topic_matches")),
        sa.UniqueConstraint(
            "client_id",
            "video_id",
            "topic_ref_id",
            "universe_hash",
            name=op.f("uq_topic_matches_client_id_video_id_topic_ref_id_universe_hash"),
        ),
    )
    op.create_index(
        "ix_topic_matches_client_id_topic_ref_id_decision_confidence",
        "topic_matches",
        ["client_id", "topic_ref_id", "decision", "confidence"],
        unique=False,
    )
    op.create_index(
        "ix_topic_matches_client_id_video_id", "topic_matches", ["client_id", "video_id"], unique=False
    )
    op.create_index("ix_topic_matches_expires_at", "topic_matches", ["expires_at"], unique=False)
    op.create_index("ix_topic_matches_video_id", "topic_matches", ["video_id"], unique=False)
    op.create_table(
        "topic_review_items",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("video_id", sa.Text(), nullable=False),
        sa.Column("topic_ref_id", sa.Text(), nullable=False),
        sa.Column(
            "reasons", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False
        ),
        sa.Column("priority", sa.REAL(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'open'"), nullable=False),
        sa.Column("assigned_to", sa.UUID(), nullable=True),
        sa.Column("resolved_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("resolution", sa.Text(), nullable=True),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["assigned_to"],
            ["users.id"],
            name=op.f("fk_topic_review_items_assigned_to_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name=op.f("fk_topic_review_items_workspace_id_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_topic_review_items")),
    )
    op.create_index(
        "ix_topic_review_items_client_id_status_priority",
        "topic_review_items",
        ["client_id", "status", sa.literal_column("priority DESC")],
        unique=False,
    )
    op.create_index("ix_topic_review_items_video_id", "topic_review_items", ["video_id"], unique=False)
    op.create_table(
        "topic_universes",
        sa.Column("profile_version", sa.Integer(), nullable=False),
        sa.Column("universe_hash", sa.Text(), nullable=False),
        sa.Column("engine_version", sa.Text(), nullable=False),
        sa.Column(
            "embedder_model_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "stats",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "warnings", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False
        ),
        sa.Column("built_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name=op.f("fk_topic_universes_workspace_id_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("client_id", "profile_version", name=op.f("pk_topic_universes")),
        sa.UniqueConstraint("universe_hash", name=op.f("uq_topic_universes_universe_hash")),
    )
    op.create_table(
        "news_signals",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("story_id", sa.UUID(), nullable=False),
        sa.Column("judgement_id", sa.UUID(), nullable=False),
        sa.Column("profile_version", sa.Integer(), nullable=False),
        sa.Column("entity", sa.Text(), nullable=False),
        sa.Column(
            "aliases", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False
        ),
        sa.Column("entity_kind", sa.Text(), nullable=False),
        sa.Column("is_competitor_entity", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "topic_ref_ids",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("first_seen_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("event_date", sa.Date(), nullable=True),
        sa.Column(
            "sources",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "credibility",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("demand", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("demand_check_id", sa.UUID(), nullable=True),
        sa.Column("demand_video_ids", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("demand_expires_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("verification_status", sa.Text(), server_default=sa.text("'active'"), nullable=False),
        sa.Column("superseded_by", sa.UUID(), nullable=True),
        sa.Column("relevance_score", sa.REAL(), nullable=False),
        sa.Column("urgency", sa.Text(), nullable=False),
        sa.Column("half_life_hours", sa.Integer(), nullable=False),
        sa.Column("expires_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "refresh_keywords",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("refresh_eligible", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "safety",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("status", sa.Text(), server_default=sa.text("'new'"), nullable=False),
        sa.Column("suppression_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "status IN ('new', 'drafted', 'shown', 'dismissed', 'used', 'suppressed', 'expired')",
            name=op.f("ck_news_signals_status"),
        ),
        sa.CheckConstraint(
            "urgency IN ('now', 'this_week', 'evergreen_window')", name=op.f("ck_news_signals_urgency")
        ),
        sa.CheckConstraint(
            "verification_status IN ('active', 'disputed', 'retracted', 'superseded')",
            name=op.f("ck_news_signals_verification_status"),
        ),
        sa.ForeignKeyConstraint(
            ["judgement_id"],
            ["news_judgements.id"],
            name=op.f("fk_news_signals_judgement_id_news_judgements"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by"],
            ["news_signals.id"],
            name=op.f("fk_news_signals_superseded_by_news_signals"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name=op.f("fk_news_signals_workspace_id_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_news_signals")),
    )
    op.create_index(
        "ix_news_signals_client_id_status_expires_at",
        "news_signals",
        ["client_id", "status", "expires_at"],
        unique=False,
    )
    op.create_index("ix_news_signals_created_at", "news_signals", ["created_at"], unique=False)
    op.create_index(
        "ix_news_signals_demand_expires_at",
        "news_signals",
        ["demand_expires_at"],
        unique=False,
        postgresql_where=sa.text("demand_expires_at IS NOT NULL"),
    )
    op.create_index(op.f("ix_news_signals_judgement_id"), "news_signals", ["judgement_id"], unique=False)
    op.create_table(
        "recommendation_notices",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("window_days", sa.SmallInteger(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("rec_type", sa.Text(), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("payload_schema_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "rec_type IS NULL OR rec_type IN ('video_idea', 'title_pattern', 'thumbnail_pattern', 'format', 'creator', 'news_opportunity', 'description_refresh')",
            name=op.f("ck_recommendation_notices_rec_type"),
        ),
        sa.CheckConstraint("window_days IN (7, 30, 90)", name=op.f("ck_recommendation_notices_window_days")),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["pipeline_runs.id"],
            name=op.f("fk_recommendation_notices_run_id_pipeline_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name=op.f("fk_recommendation_notices_workspace_id_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_recommendation_notices")),
    )
    op.create_index(
        "ix_recommendation_notices_created_at", "recommendation_notices", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_recommendation_notices_run_id"), "recommendation_notices", ["run_id"], unique=False
    )
    op.create_table(
        "recommendation_suppressions",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=True),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("stable_key", sa.Text(), nullable=False),
        sa.Column("phase", sa.Text(), nullable=False),
        sa.Column("gate", sa.Text(), nullable=False),
        sa.Column("reason_code", sa.Text(), nullable=False),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("draft", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("draft_schema_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "type IN ('video_idea', 'title_pattern', 'thumbnail_pattern', 'format', 'creator', 'news_opportunity', 'description_refresh')",
            name=op.f("ck_recommendation_suppressions_type"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["pipeline_runs.id"],
            name=op.f("fk_recommendation_suppressions_run_id_pipeline_runs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name=op.f("fk_recommendation_suppressions_workspace_id_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_recommendation_suppressions")),
    )
    op.create_index(
        "ix_recommendation_suppressions_client_id_created_at",
        "recommendation_suppressions",
        ["client_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_recommendation_suppressions_created_at",
        "recommendation_suppressions",
        ["created_at"],
        unique=False,
        postgresql_where=sa.text("draft IS NOT NULL"),
    )
    op.create_index(
        "ix_recommendation_suppressions_gate_reason_code",
        "recommendation_suppressions",
        ["gate", "reason_code"],
        unique=False,
    )
    op.create_index(
        op.f("ix_recommendation_suppressions_run_id"), "recommendation_suppressions", ["run_id"], unique=False
    )
    op.create_table(
        "recommendations",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("rec_id", sa.Text(), nullable=False),
        sa.Column("stable_key", sa.Text(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("window_days", sa.SmallInteger(), nullable=False),
        sa.Column("rank", sa.SmallInteger(), nullable=True),
        sa.Column("status", sa.Text(), server_default=sa.text("'shown'"), nullable=False),
        sa.Column("lens", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("buyer_question_id", sa.UUID(), nullable=True),
        sa.Column("score_total", sa.REAL(), nullable=True),
        sa.Column(
            "score_components",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("payload_schema_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("draft", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("draft_schema_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "writer_meta",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "referenced_video_ids",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column(
            "referenced_channel_ids",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("stale_reference_count", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("uses_authorized_data", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("first_seen_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("last_seen_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("generated_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("api_data_observed_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("expires_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("redacted_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "status IN ('shown', 'expired', 'hidden')", name=op.f("ck_recommendations_status")
        ),
        sa.CheckConstraint(
            "type IN ('video_idea', 'title_pattern', 'thumbnail_pattern', 'format', 'creator', 'news_opportunity', 'description_refresh')",
            name=op.f("ck_recommendations_type"),
        ),
        sa.CheckConstraint("window_days IN (7, 30, 90)", name=op.f("ck_recommendations_window_days")),
        sa.ForeignKeyConstraint(
            ["buyer_question_id"],
            ["buyer_questions.id"],
            name=op.f("fk_recommendations_buyer_question_id_buyer_questions"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["pipeline_runs.id"],
            name=op.f("fk_recommendations_run_id_pipeline_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name=op.f("fk_recommendations_workspace_id_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_recommendations")),
        sa.UniqueConstraint("run_id", "stable_key", name=op.f("uq_recommendations_run_id_stable_key")),
    )
    op.create_index(
        "ix_recommendations_api_data_observed_at",
        "recommendations",
        ["api_data_observed_at"],
        unique=False,
        postgresql_where=sa.text("redacted_at IS NULL"),
    )
    op.create_index(
        "ix_recommendations_client_id_rec_id", "recommendations", ["client_id", "rec_id"], unique=False
    )
    op.create_index(
        "ix_recommendations_client_id_stable_key",
        "recommendations",
        ["client_id", "stable_key"],
        unique=False,
    )
    op.create_index(
        "ix_recommendations_client_id_window_days_status",
        "recommendations",
        ["client_id", "window_days", "status"],
        unique=False,
    )
    op.create_index(
        "ix_recommendations_expires_at",
        "recommendations",
        ["expires_at"],
        unique=False,
        postgresql_where=sa.text("status = 'shown'"),
    )
    op.create_index(
        "ix_recommendations_referenced_channel_ids",
        "recommendations",
        ["referenced_channel_ids"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_index(
        "ix_recommendations_referenced_video_ids",
        "recommendations",
        ["referenced_video_ids"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_table(
        "refresh_eligibility",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("cadence_days", sa.SmallInteger(), nullable=False),
        sa.Column("keyword_refresh_need", sa.REAL(), nullable=False),
        sa.Column(
            "components",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("evergreen_video_count", sa.Integer(), nullable=False),
        sa.Column("eligible_video_count", sa.Integer(), nullable=False),
        sa.Column("velocity_basis", sa.Text(), nullable=False),
        sa.Column(
            "reasons",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("computed_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("last_keyword_eval_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["pipeline_runs.id"],
            name=op.f("fk_refresh_eligibility_run_id_pipeline_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name=op.f("fk_refresh_eligibility_workspace_id_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refresh_eligibility")),
    )
    op.create_index(
        "ix_refresh_eligibility_client_id_computed_at",
        "refresh_eligibility",
        ["client_id", sa.literal_column("computed_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_refresh_eligibility_computed_at", "refresh_eligibility", ["computed_at"], unique=False
    )
    op.create_index(op.f("ix_refresh_eligibility_run_id"), "refresh_eligibility", ["run_id"], unique=False)
    op.create_table(
        "topic_refs",
        sa.Column("profile_version", sa.Integer(), nullable=False),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("source_item_id", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("definition", sa.Text(), nullable=True),
        sa.Column(
            "parent_ids", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False
        ),
        sa.Column(
            "offering_ids",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("funnel_stage", sa.Text(), nullable=True),
        sa.Column(
            "facets", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False
        ),
        sa.Column(
            "core_entity_terms",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column(
            "locality_terms",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column(
            "negative_contexts",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("weight", sa.REAL(), server_default=sa.text("1.0"), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'active'"), nullable=False),
        sa.Column("expires_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("fingerprint", sa.Text(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "funnel_stage IS NULL OR funnel_stage IN ('awareness', 'consideration', 'decision', 'post_purchase')",
            name=op.f("ck_topic_refs_funnel_stage"),
        ),
        sa.CheckConstraint(
            "kind IN ('keyword', 'offering', 'buyer_question', 'news_entity')",
            name=op.f("ck_topic_refs_kind"),
        ),
        sa.CheckConstraint("status IN ('active', 'disabled')", name=op.f("ck_topic_refs_status")),
        sa.ForeignKeyConstraint(
            ["client_id", "profile_version"],
            ["topic_universes.client_id", "topic_universes.profile_version"],
            name=op.f("fk_topic_refs_client_id_profile_version_topic_universes"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name=op.f("fk_topic_refs_workspace_id_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("client_id", "profile_version", "id", name=op.f("pk_topic_refs")),
    )
    op.create_index("ix_topic_refs_client_id_id", "topic_refs", ["client_id", "id"], unique=False)
    op.create_index(
        "ix_topic_refs_expires_at",
        "topic_refs",
        ["expires_at"],
        unique=False,
        postgresql_where=sa.text("expires_at IS NOT NULL"),
    )
    op.create_table(
        "white_space_notes",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("buyer_question_id", sa.UUID(), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("payload_schema_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["buyer_question_id"],
            ["buyer_questions.id"],
            name=op.f("fk_white_space_notes_buyer_question_id_buyer_questions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["pipeline_runs.id"],
            name=op.f("fk_white_space_notes_run_id_pipeline_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name=op.f("fk_white_space_notes_workspace_id_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_white_space_notes")),
    )
    op.create_index(
        "ix_white_space_notes_client_id_run_id", "white_space_notes", ["client_id", "run_id"], unique=False
    )
    op.create_index("ix_white_space_notes_created_at", "white_space_notes", ["created_at"], unique=False)
    op.create_table(
        "description_refreshes",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("video_id", sa.Text(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("recommendation_id", sa.UUID(), nullable=True),
        sa.Column(
            "triggers",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "operations",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("operations_schema_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "advisories",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "suggested_tags",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column(
            "keywords_added",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column(
            "dedupe_keys", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False
        ),
        sa.Column("inserted_bytes", sa.Integer(), nullable=False),
        sa.Column("resulting_bytes", sa.Integer(), nullable=False),
        sa.Column("expected_effect", sa.Text(), nullable=False),
        sa.Column(
            "confidence",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("priority", sa.REAL(), nullable=False),
        sa.Column("expires_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'proposed'"), nullable=False),
        sa.Column("description_hash_at_generation", sa.Text(), nullable=False),
        sa.Column("review_required", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("generated_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("applied_at", sa.Date(), nullable=True),
        sa.Column("applied_source", sa.Text(), nullable=True),
        sa.Column("dismissed_reason", sa.Text(), nullable=True),
        sa.Column("redacted_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "updated_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "status IN ('proposed', 'dismissed', 'applied_claimed', 'applied_detected', 'expired', 'withdrawn', 'superseded')",
            name=op.f("ck_description_refreshes_status"),
        ),
        sa.CheckConstraint("resulting_bytes <= 5000", name=op.f("ck_description_refreshes_resulting_bytes")),
        sa.ForeignKeyConstraint(
            ["recommendation_id"],
            ["recommendations.id"],
            name=op.f("fk_description_refreshes_recommendation_id_recommendations"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["pipeline_runs.id"],
            name=op.f("fk_description_refreshes_run_id_pipeline_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name=op.f("fk_description_refreshes_workspace_id_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_description_refreshes")),
    )
    op.create_index(
        "ix_description_refreshes_client_id_status_priority",
        "description_refreshes",
        ["client_id", "status", sa.literal_column("priority DESC")],
        unique=False,
    )
    op.create_index(
        "ix_description_refreshes_client_id_video_id_status",
        "description_refreshes",
        ["client_id", "video_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_description_refreshes_dedupe_keys",
        "description_refreshes",
        ["dedupe_keys"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_index(
        "ix_description_refreshes_expires_at", "description_refreshes", ["expires_at"], unique=False
    )
    op.create_index(
        "ix_description_refreshes_generated_at",
        "description_refreshes",
        ["generated_at"],
        unique=False,
        postgresql_where=sa.text("redacted_at IS NULL"),
    )
    op.create_index(
        op.f("ix_description_refreshes_run_id"), "description_refreshes", ["run_id"], unique=False
    )
    op.create_table(
        "news_review_queue",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("signal_id", sa.UUID(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("assigned_to", sa.UUID(), nullable=True),
        sa.Column("decision", sa.Text(), nullable=True),
        sa.Column("decided_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["assigned_to"],
            ["users.id"],
            name=op.f("fk_news_review_queue_assigned_to_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["signal_id"],
            ["news_signals.id"],
            name=op.f("fk_news_review_queue_signal_id_news_signals"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name=op.f("fk_news_review_queue_workspace_id_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_news_review_queue")),
    )
    op.create_index(
        "ix_news_review_queue_client_id_decision",
        "news_review_queue",
        ["client_id", "decision"],
        unique=False,
    )
    op.create_index("ix_news_review_queue_created_at", "news_review_queue", ["created_at"], unique=False)
    op.create_index(op.f("ix_news_review_queue_signal_id"), "news_review_queue", ["signal_id"], unique=False)
    op.create_table(
        "recommendation_copy",
        sa.Column("draft_id", sa.Text(), nullable=False),
        sa.Column("recommendation_id", sa.UUID(), nullable=True),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("copy_source", sa.Text(), nullable=False),
        sa.Column("degrade_reason", sa.Text(), nullable=True),
        sa.Column("prompt_id", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("attempts", sa.SmallInteger(), nullable=False),
        sa.Column(
            "reason_fact_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "fit_fact_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "violations_last",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("critique", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("language", sa.Text(), nullable=False),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["recommendation_id"],
            ["recommendations.id"],
            name=op.f("fk_recommendation_copy_recommendation_id_recommendations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["pipeline_runs.id"],
            name=op.f("fk_recommendation_copy_run_id_pipeline_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name=op.f("fk_recommendation_copy_workspace_id_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("draft_id", name=op.f("pk_recommendation_copy")),
    )
    op.create_index(
        "ix_recommendation_copy_client_id_created_at",
        "recommendation_copy",
        ["client_id", "created_at"],
        unique=False,
    )
    op.create_index("ix_recommendation_copy_created_at", "recommendation_copy", ["created_at"], unique=False)
    op.create_index(
        op.f("ix_recommendation_copy_recommendation_id"),
        "recommendation_copy",
        ["recommendation_id"],
        unique=False,
    )
    op.create_index(op.f("ix_recommendation_copy_run_id"), "recommendation_copy", ["run_id"], unique=False)
    op.create_table(
        "recommendation_feedback",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("recommendation_id", sa.UUID(), nullable=True),
        sa.Column("stable_key", sa.Text(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("status", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("linked_video_id", sa.Text(), nullable=True),
        sa.Column("linked_video_verified_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "status IS NULL OR status IN ('saved', 'dismissed', 'done', 'snoozed')",
            name=op.f("ck_recommendation_feedback_status"),
        ),
        sa.ForeignKeyConstraint(
            ["recommendation_id"],
            ["recommendations.id"],
            name=op.f("fk_recommendation_feedback_recommendation_id_recommendations"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_recommendation_feedback_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name=op.f("fk_recommendation_feedback_workspace_id_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_recommendation_feedback")),
        sa.UniqueConstraint(
            "client_id",
            "stable_key",
            "user_id",
            name=op.f("uq_recommendation_feedback_client_id_stable_key_user_id"),
        ),
    )
    op.create_index(
        "ix_recommendation_feedback_client_id_created_at",
        "recommendation_feedback",
        ["client_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_recommendation_feedback_linked_video_verified_at",
        "recommendation_feedback",
        ["linked_video_verified_at"],
        unique=False,
        postgresql_where=sa.text("linked_video_id IS NOT NULL"),
    )
    op.create_index(
        "ix_recommendation_feedback_recommendation_id",
        "recommendation_feedback",
        ["recommendation_id"],
        unique=False,
    )
    op.create_table(
        "recommendation_references",
        sa.Column("recommendation_id", sa.UUID(), nullable=False),
        sa.Column("ordinal", sa.SmallInteger(), nullable=False),
        sa.Column("video_id", sa.Text(), nullable=False),
        sa.Column("channel_id", sa.Text(), nullable=False),
        sa.Column("channel_role", sa.Text(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "channel_role IN ('own', 'competitor', 'niche', 'benchmark', 'creator')",
            name=op.f("ck_recommendation_references_channel_role"),
        ),
        sa.ForeignKeyConstraint(
            ["recommendation_id"],
            ["recommendations.id"],
            name=op.f("fk_recommendation_references_recommendation_id_recommendations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name=op.f("fk_recommendation_references_workspace_id_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("recommendation_id", "ordinal", name=op.f("pk_recommendation_references")),
    )
    op.create_index(
        op.f("ix_recommendation_references_video_id"), "recommendation_references", ["video_id"], unique=False
    )
    op.create_table(
        "topic_aliases",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("topic_ref_id", sa.Text(), nullable=False),
        sa.Column("profile_version", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("norm_key", sa.Text(), nullable=False),
        sa.Column("phonetic_key", sa.Text(), nullable=True),
        sa.Column("lang", sa.Text(), nullable=True),
        sa.Column("script", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("origin", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("anchor_text", sa.Text(), nullable=False),
        sa.Column("match_mode", sa.Text(), nullable=False),
        sa.Column("weight", sa.REAL(), nullable=False),
        sa.Column("ambiguous", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("rejected_reason", sa.Text(), nullable=True),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["client_id", "profile_version", "topic_ref_id"],
            ["topic_refs.client_id", "topic_refs.profile_version", "topic_refs.id"],
            name=op.f("fk_topic_aliases_client_id_profile_version_topic_ref_id_topic_refs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name=op.f("fk_topic_aliases_workspace_id_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_topic_aliases")),
    )
    op.create_index(
        "ix_topic_aliases_client_id_norm_key", "topic_aliases", ["client_id", "norm_key"], unique=False
    )
    op.create_index(
        "ix_topic_aliases_client_id_topic_ref_id",
        "topic_aliases",
        ["client_id", "topic_ref_id"],
        unique=False,
    )
    op.create_table(
        "refresh_impact",
        sa.Column("refresh_id", sa.UUID(), nullable=False),
        sa.Column("applied_at", sa.Date(), nullable=False),
        sa.Column("applied_source", sa.Text(), nullable=False),
        sa.Column("pre_window_days", sa.SmallInteger(), nullable=False),
        sa.Column("post_window_days", sa.SmallInteger(), nullable=False),
        sa.Column("pre_velocity", sa.REAL(), nullable=False),
        sa.Column("post_velocity", sa.REAL(), nullable=False),
        sa.Column("ratio", sa.REAL(), nullable=False),
        sa.Column("control_ratio", sa.REAL(), nullable=True),
        sa.Column("adjusted_ratio", sa.REAL(), nullable=True),
        sa.Column("owner_search_views_pre", sa.Integer(), nullable=True),
        sa.Column("owner_search_views_post", sa.Integer(), nullable=True),
        sa.Column("verdict", sa.Text(), nullable=False),
        sa.Column(
            "caveats",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("computed_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["refresh_id"],
            ["description_refreshes.id"],
            name=op.f("fk_refresh_impact_refresh_id_description_refreshes"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name=op.f("fk_refresh_impact_workspace_id_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("refresh_id", name=op.f("pk_refresh_impact")),
    )
    op.create_index(op.f("ix_refresh_impact_computed_at"), "refresh_impact", ["computed_at"], unique=False)
    op.create_foreign_key(
        op.f("fk_news_stories_representative_item_id_news_items"),
        "news_stories",
        "news_items",
        ["representative_item_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_news_stories_representative_item_id_news_items"), "news_stories", type_="foreignkey"
    )
    op.drop_table("refresh_impact")
    op.drop_table("topic_aliases")
    op.drop_table("recommendation_references")
    op.drop_table("recommendation_feedback")
    op.drop_table("recommendation_copy")
    op.drop_table("news_review_queue")
    op.drop_table("description_refreshes")
    op.drop_table("white_space_notes")
    op.drop_table("topic_refs")
    op.drop_table("refresh_eligibility")
    op.drop_table("recommendations")
    op.drop_table("recommendation_suppressions")
    op.drop_table("recommendation_notices")
    op.drop_table("news_signals")
    op.drop_table("topic_universes")
    op.drop_table("topic_review_items")
    op.drop_table("topic_matches")
    op.drop_table("topic_match_overrides")
    op.drop_table("ref_embeddings")
    op.drop_table("recommendation_outcomes")
    op.drop_table("news_query_subscriptions")
    op.drop_table("news_judgements")
    op.drop_table("llm_adjudications")
    op.drop_table("client_recommend_config")
    op.drop_table("answer_directness")
    op.drop_table("review_rating")
    op.drop_table("news_query_runs")
    op.drop_table("news_items")
    op.drop_table("label_gold")
    op.drop_table("label")
    op.drop_table("eval_result")
    op.drop_table("eval_pattern_spec")
    op.drop_table("eval_outcome_video")
    op.drop_table("yt_quota_ledger")
    op.drop_table("topic_eval_labels")
    op.drop_table("source_tiers")
    op.drop_table("review_item")
    op.drop_table("quota_cache_hits_daily")
    op.drop_table("provider_usage_ledger")
    op.drop_table("outlier_eval_result")
    op.drop_table("ops_daily_rollup")
    op.drop_table("news_stories")
    op.drop_table("news_queries")
    op.drop_table("llm_result_cache")
    op.drop_table("llm_debug_payloads")
    op.drop_table("link_check_cache")
    op.drop_table("label_task")
    op.drop_table("gate_tuning_proposal")
    op.drop_table("eval_freeze")
    op.drop_table("eval_config")
    op.drop_table("alert_event")
