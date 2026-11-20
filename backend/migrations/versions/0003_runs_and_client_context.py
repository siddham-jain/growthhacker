"""pipeline runs, website context, competitors, own analytics

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TRACKED_CHANNELS_VIEW_SQL = """
CREATE VIEW v_tracked_channels AS
WITH live_clients AS (
  SELECT id, workspace_id, own_channel_id, own_channel_status FROM clients WHERE deleted_at IS NULL
),
latest_sets AS (
  SELECT DISTINCT ON (competitor_sets.client_id) competitor_sets.*
  FROM competitor_sets JOIN live_clients ON live_clients.id = competitor_sets.client_id
  ORDER BY competitor_sets.client_id, competitor_sets.version DESC
)
SELECT workspace_id, id AS client_id, own_channel_id AS channel_id, 'own'::text AS role
FROM live_clients
WHERE own_channel_id IS NOT NULL AND own_channel_status IN ('proposed', 'verified')
UNION
SELECT workspace_id, client_id, unnest(competitor_channel_ids), 'competitor' FROM latest_sets
UNION
SELECT workspace_id, client_id, unnest(niche_channel_ids), 'niche' FROM latest_sets
UNION
SELECT workspace_id, client_id, unnest(benchmark_channel_ids), 'benchmark' FROM latest_sets
UNION
SELECT workspace_id, client_id, unnest(creator_seed_channel_ids), 'creator' FROM latest_sets
"""


def upgrade() -> None:
    op.create_table(
        "buyer_questions",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("language", sa.Text(), nullable=False),
        sa.Column(
            "variants",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("funnel_stage", sa.Text(), nullable=False),
        sa.Column("intent", sa.Text(), nullable=False),
        sa.Column(
            "offering_ids",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("conversion_action_id", sa.Text(), nullable=True),
        sa.Column("geo_modifier", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_ref", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default=sa.text("'proposed'"), nullable=False),
        sa.Column("priority", sa.SmallInteger(), server_default=sa.text("2"), nullable=False),
        sa.Column("ai_cited_video_ids", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("ai_citations_observed_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "meta",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
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
        sa.CheckConstraint(
            "funnel_stage IN ('awareness', 'consideration', 'decision', 'post_purchase')",
            name=op.f("ck_buyer_questions_funnel_stage"),
        ),
        sa.CheckConstraint(
            "source IN ('website_faq', 'website_derived', 'user', 'spotlit', 'comments', 'import')",
            name=op.f("ck_buyer_questions_source"),
        ),
        sa.CheckConstraint(
            "status IN ('proposed', 'confirmed', 'rejected', 'retired')",
            name=op.f("ck_buyer_questions_status"),
        ),
        sa.CheckConstraint("priority BETWEEN 1 AND 3", name=op.f("ck_buyer_questions_priority")),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name=op.f("fk_buyer_questions_workspace_id_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_buyer_questions")),
        sa.UniqueConstraint(
            "client_id",
            "normalized_text",
            "language",
            name=op.f("uq_buyer_questions_client_id_normalized_text_language"),
        ),
    )
    op.create_index(
        "ix_buyer_questions_ai_citations_observed_at",
        "buyer_questions",
        ["ai_citations_observed_at"],
        unique=False,
        postgresql_where=sa.text("ai_citations_observed_at IS NOT NULL"),
    )
    op.create_index(
        "ix_buyer_questions_client_id_status", "buyer_questions", ["client_id", "status"], unique=False
    )
    op.create_table(
        "competitor_sets",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "competitor_channel_ids",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column(
            "niche_channel_ids",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column(
            "benchmark_channel_ids",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column(
            "creator_seed_channel_ids",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column(
            "contains_content_competitors", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("confirmed_by", sa.Text(), nullable=False),
        sa.Column("confirmed_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "ids_verified_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name=op.f("fk_competitor_sets_workspace_id_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_competitor_sets")),
        sa.UniqueConstraint("client_id", "version", name=op.f("uq_competitor_sets_client_id_version")),
    )
    op.create_index(
        "ix_competitor_sets_ids_verified_at", "competitor_sets", ["ids_verified_at"], unique=False
    )
    op.create_table(
        "pipeline_runs",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("trigger", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'queued'"), nullable=False),
        sa.Column("attempt", sa.SmallInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column("resume_of_run_id", sa.UUID(), nullable=True),
        sa.Column("superseded_by_run_id", sa.UUID(), nullable=True),
        sa.Column("follow_up_requested", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("window_days", sa.SmallInteger(), nullable=True),
        sa.Column(
            "as_of", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "config_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("config_hash", sa.Text(), nullable=False),
        sa.Column("params_hash", sa.Text(), nullable=True),
        sa.Column("queue", sa.Text(), nullable=False),
        sa.Column("priority", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("arq_job_id", sa.Text(), nullable=True),
        sa.Column("lease_owner", sa.Text(), nullable=True),
        sa.Column("lease_expires_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("event_seq", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("progress_pct", sa.REAL(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "current_stages",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column(
            "pinned_channel_ids",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column(
            "notices",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "usage",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "budget",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "timings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=True),
        sa.Column("requested_by", sa.Text(), nullable=True),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "queued_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("started_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("finished_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("cancel_requested_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('onboarding', 'analysis', 'daily_refresh', 'cards', 'news_scan', 'recrawl', 'backtest')",
            name=op.f("ck_pipeline_runs_kind"),
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'cancelling', 'succeeded', 'partial', 'failed', 'cancelled', 'superseded')",
            name=op.f("ck_pipeline_runs_status"),
        ),
        sa.CheckConstraint(
            "trigger IN ('user', 'schedule', 'system', 'resume', 'evaluation')",
            name=op.f("ck_pipeline_runs_trigger"),
        ),
        sa.CheckConstraint(
            "window_days IS NULL OR window_days IN (7, 30, 90)", name=op.f("ck_pipeline_runs_window_days")
        ),
        sa.ForeignKeyConstraint(
            ["resume_of_run_id"],
            ["pipeline_runs.id"],
            name=op.f("fk_pipeline_runs_resume_of_run_id_pipeline_runs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by_run_id"],
            ["pipeline_runs.id"],
            name=op.f("fk_pipeline_runs_superseded_by_run_id_pipeline_runs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name=op.f("fk_pipeline_runs_workspace_id_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pipeline_runs")),
    )
    op.create_index(
        "ix_pipeline_runs_client_id_created_at",
        "pipeline_runs",
        ["client_id", sa.literal_column("created_at DESC")],
        unique=False,
    )
    op.create_index("ix_pipeline_runs_finished_at", "pipeline_runs", ["finished_at"], unique=False)
    op.create_index(
        "ix_pipeline_runs_status_lease_expires_at",
        "pipeline_runs",
        ["status", "lease_expires_at"],
        unique=False,
        postgresql_where=sa.text("status IN ('queued', 'running', 'cancelling')"),
    )
    op.create_index(
        "ix_pipeline_runs_workspace_id_active",
        "pipeline_runs",
        ["workspace_id"],
        unique=False,
        postgresql_where=sa.text("status IN ('queued', 'running', 'cancelling')"),
    )
    op.create_index(
        "uq_pipeline_runs_active_per_client",
        "pipeline_runs",
        ["client_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running', 'cancelling')"),
    )
    op.create_index(
        "uq_pipeline_runs_workspace_id_idempotency_key",
        "pipeline_runs",
        ["workspace_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.create_table(
        "profile_diffs",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("from_version", sa.Integer(), nullable=True),
        sa.Column("to_version", sa.Integer(), nullable=False),
        sa.Column(
            "diff",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("significance", sa.Text(), nullable=False),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name=op.f("fk_profile_diffs_workspace_id_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_profile_diffs")),
    )
    op.create_index(
        "ix_profile_diffs_client_id_created_at", "profile_diffs", ["client_id", "created_at"], unique=False
    )
    op.create_table(
        "profile_edits",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("base_version", sa.Integer(), nullable=False),
        sa.Column("result_version", sa.Integer(), nullable=True),
        sa.Column(
            "ops",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("actor_user_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name=op.f("fk_profile_edits_actor_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name=op.f("fk_profile_edits_workspace_id_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_profile_edits")),
    )
    op.create_index(
        "ix_profile_edits_client_id_created_at", "profile_edits", ["client_id", "created_at"], unique=False
    )
    op.create_table(
        "profile_tombstones",
        sa.Column("collection", sa.Text(), nullable=False),
        sa.Column("item_key", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", postgresql.BYTEA(), nullable=True),
        sa.Column("embedding_model", sa.Text(), nullable=True),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name=op.f("fk_profile_tombstones_workspace_id_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("client_id", "collection", "item_key", name=op.f("pk_profile_tombstones")),
    )
    op.create_table(
        "websites",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("input_url", sa.Text(), nullable=False),
        sa.Column("canonical_origin", sa.Text(), nullable=False),
        sa.Column("registrable_domain", sa.Text(), nullable=False),
        sa.Column(
            "scope_hosts", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False
        ),
        sa.Column("scope_mode", sa.Text(), nullable=False),
        sa.Column("focus_path", sa.Text(), nullable=True),
        sa.Column(
            "country_sites",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "related_brands",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("ownership_attested", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("robots_status", sa.Text(), nullable=True),
        sa.Column("last_crawl_run_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name=op.f("fk_websites_workspace_id_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_websites")),
        sa.UniqueConstraint(
            "client_id",
            "registrable_domain",
            "focus_path",
            name=op.f("uq_websites_client_id_registrable_domain_focus_path"),
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index("ix_websites_registrable_domain", "websites", ["registrable_domain"], unique=False)
    op.create_table(
        "active_result_sets",
        sa.Column("window_days", sa.SmallInteger(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("published_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "stale_reasons",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column(
            "superseded_history",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.CheckConstraint("window_days IN (7, 30, 90)", name=op.f("ck_active_result_sets_window_days")),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["pipeline_runs.id"],
            name=op.f("fk_active_result_sets_run_id_pipeline_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name=op.f("fk_active_result_sets_workspace_id_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("client_id", "window_days", name=op.f("pk_active_result_sets")),
    )
    op.create_table(
        "client_schedules",
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("cadence_hours", sa.REAL(), nullable=False),
        sa.Column("next_due_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_enqueued_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_run_id", sa.UUID(), nullable=True),
        sa.Column("last_success_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("failure_streak", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("circuit_open_until", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("paused", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.CheckConstraint("kind IN ('daily_refresh', 'news_scan')", name=op.f("ck_client_schedules_kind")),
        sa.ForeignKeyConstraint(
            ["last_run_id"],
            ["pipeline_runs.id"],
            name=op.f("fk_client_schedules_last_run_id_pipeline_runs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name=op.f("fk_client_schedules_workspace_id_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("client_id", "kind", name=op.f("pk_client_schedules")),
    )
    op.create_index(
        "ix_client_schedules_kind_next_due_at",
        "client_schedules",
        ["kind", "next_due_at"],
        unique=False,
        postgresql_where=sa.text("NOT paused"),
    )
    op.create_table(
        "competitor_discovery_runs",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=True),
        sa.Column("trigger", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'running'"), nullable=False),
        sa.Column("started_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("finished_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("yt_units", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("search_calls", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("tavily_credits", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("llm_tokens_in", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("llm_tokens_out", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("llm_usd", sa.Numeric(precision=8, scale=4), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "warnings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["pipeline_runs.id"],
            name=op.f("fk_competitor_discovery_runs_run_id_pipeline_runs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name=op.f("fk_competitor_discovery_runs_workspace_id_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_competitor_discovery_runs")),
    )
    op.create_index(
        "ix_competitor_discovery_runs_client_id_started_at",
        "competitor_discovery_runs",
        ["client_id", sa.literal_column("started_at DESC")],
        unique=False,
    )
    op.create_index(
        op.f("ix_competitor_discovery_runs_run_id"), "competitor_discovery_runs", ["run_id"], unique=False
    )
    op.create_table(
        "crawl_runs",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("website_id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=True),
        sa.Column("trigger", sa.Text(), nullable=False),
        sa.Column("profile", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'running'"), nullable=False),
        sa.Column("ua_mode", sa.Text(), nullable=True),
        sa.Column(
            "budget",
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
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("finished_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["pipeline_runs.id"],
            name=op.f("fk_crawl_runs_run_id_pipeline_runs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["website_id"],
            ["websites.id"],
            name=op.f("fk_crawl_runs_website_id_websites"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name=op.f("fk_crawl_runs_workspace_id_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_crawl_runs")),
    )
    op.create_index("ix_crawl_runs_finished_at", "crawl_runs", ["finished_at"], unique=False)
    op.create_index(op.f("ix_crawl_runs_run_id"), "crawl_runs", ["run_id"], unique=False)
    op.create_index(
        "ix_crawl_runs_website_id_started_at",
        "crawl_runs",
        ["website_id", sa.literal_column("started_at DESC")],
        unique=False,
    )
    op.create_table(
        "own_analytics_daily",
        sa.Column("video_id", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("oauth_token_id", sa.UUID(), nullable=False),
        sa.Column("views", sa.BigInteger(), nullable=False),
        sa.Column("engaged_views", sa.BigInteger(), nullable=True),
        sa.Column("estimated_minutes_watched", sa.BigInteger(), nullable=True),
        sa.Column("average_view_duration_s", sa.Integer(), nullable=True),
        sa.Column("subscribers_gained", sa.Integer(), nullable=True),
        sa.Column("subscribers_lost", sa.Integer(), nullable=True),
        sa.Column("likes", sa.BigInteger(), nullable=True),
        sa.Column("comments", sa.BigInteger(), nullable=True),
        sa.Column("shares", sa.BigInteger(), nullable=True),
        sa.Column("creator_content_type", sa.Text(), nullable=True),
        sa.Column("fetched_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["oauth_token_id"],
            ["oauth_tokens.id"],
            name=op.f("fk_own_analytics_daily_oauth_token_id_oauth_tokens"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name=op.f("fk_own_analytics_daily_workspace_id_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("client_id", "video_id", "day", name=op.f("pk_own_analytics_daily")),
    )
    op.create_index(
        "ix_own_analytics_daily_client_id_day", "own_analytics_daily", ["client_id", "day"], unique=False
    )
    op.create_index(
        op.f("ix_own_analytics_daily_fetched_at"), "own_analytics_daily", ["fetched_at"], unique=False
    )
    op.create_index(
        op.f("ix_own_analytics_daily_oauth_token_id"), "own_analytics_daily", ["oauth_token_id"], unique=False
    )
    op.create_table(
        "own_search_term",
        sa.Column("video_id", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("term", sa.Text(), nullable=False),
        sa.Column("oauth_token_id", sa.UUID(), nullable=False),
        sa.Column("views", sa.BigInteger(), nullable=False),
        sa.Column("estimated_minutes_watched", sa.BigInteger(), nullable=True),
        sa.Column("fetched_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["oauth_token_id"],
            ["oauth_tokens.id"],
            name=op.f("fk_own_search_term_oauth_token_id_oauth_tokens"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name=op.f("fk_own_search_term_workspace_id_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "client_id", "video_id", "period_start", "period_end", "term", name=op.f("pk_own_search_term")
        ),
    )
    op.create_index(op.f("ix_own_search_term_fetched_at"), "own_search_term", ["fetched_at"], unique=False)
    op.create_index(
        op.f("ix_own_search_term_oauth_token_id"), "own_search_term", ["oauth_token_id"], unique=False
    )
    op.create_table(
        "own_traffic_source",
        sa.Column("video_id", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("oauth_token_id", sa.UUID(), nullable=False),
        sa.Column("views", sa.BigInteger(), nullable=False),
        sa.Column("estimated_minutes_watched", sa.BigInteger(), nullable=True),
        sa.Column("fetched_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["oauth_token_id"],
            ["oauth_tokens.id"],
            name=op.f("fk_own_traffic_source_oauth_token_id_oauth_tokens"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name=op.f("fk_own_traffic_source_workspace_id_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "client_id",
            "video_id",
            "period_start",
            "period_end",
            "source_type",
            name=op.f("pk_own_traffic_source"),
        ),
    )
    op.create_index(
        op.f("ix_own_traffic_source_fetched_at"), "own_traffic_source", ["fetched_at"], unique=False
    )
    op.create_index(
        op.f("ix_own_traffic_source_oauth_token_id"), "own_traffic_source", ["oauth_token_id"], unique=False
    )
    op.create_table(
        "run_artifacts",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("fingerprint", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("contains_api_data", sa.Boolean(), nullable=False),
        sa.Column("uses_authorized_data", sa.Boolean(), server_default=sa.text("false"), nullable=False),
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
        sa.Column("api_data_observed_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("expires_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.CheckConstraint("pg_column_size(payload) < 2097152", name=op.f("ck_run_artifacts_payload_size")),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["pipeline_runs.id"],
            name=op.f("fk_run_artifacts_run_id_pipeline_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name=op.f("fk_run_artifacts_workspace_id_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_run_artifacts")),
        sa.UniqueConstraint("run_id", "key", name=op.f("uq_run_artifacts_run_id_key")),
    )
    op.create_index(
        "ix_run_artifacts_client_id_key_fingerprint_created_at",
        "run_artifacts",
        ["client_id", "key", "fingerprint", sa.literal_column("created_at DESC")],
        unique=False,
    )
    op.create_index(op.f("ix_run_artifacts_expires_at"), "run_artifacts", ["expires_at"], unique=False)
    op.create_index(
        "ix_run_artifacts_referenced_video_ids",
        "run_artifacts",
        ["referenced_video_ids"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_table(
        "run_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("stage", sa.Text(), nullable=True),
        sa.Column("step", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=True),
        sa.Column("level", sa.Text(), server_default=sa.text("'info'"), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("counts", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("percent", sa.REAL(), nullable=True),
        sa.Column("stage_percent", sa.REAL(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.CheckConstraint("level IN ('info', 'warning', 'error')", name=op.f("ck_run_events_level")),
        sa.CheckConstraint(
            "type IN ('run.queued', 'run.started', 'run.progress', 'run.awaiting_confirmation', 'run.succeeded', 'run.partial', 'run.failed', 'run.cancelled', 'run.superseded', 'stage.started', 'stage.step', 'stage.progress', 'stage.retrying', 'stage.reused', 'stage.succeeded', 'stage.degraded', 'stage.failed', 'stage.skipped', 'insight', 'notice', 'results.published')",
            name=op.f("ck_run_events_type"),
        ),
        sa.CheckConstraint("char_length(message) <= 200", name=op.f("ck_run_events_message_length")),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["pipeline_runs.id"],
            name=op.f("fk_run_events_run_id_pipeline_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name=op.f("fk_run_events_workspace_id_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_run_events")),
        sa.UniqueConstraint("run_id", "seq", name=op.f("uq_run_events_run_id_seq")),
    )
    op.create_index(op.f("ix_run_events_created_at"), "run_events", ["created_at"], unique=False)
    op.create_table(
        "stage_runs",
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("stage_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("attempt", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("input_fingerprint", sa.Text(), nullable=True),
        sa.Column("reused_from_run_id", sa.UUID(), nullable=True),
        sa.Column("started_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("finished_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column(
            "counts",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "usage",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "warnings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'reused', 'degraded', 'failed', 'skipped', 'cancelled', 'interrupted')",
            name=op.f("ck_stage_runs_status"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["pipeline_runs.id"],
            name=op.f("fk_stage_runs_run_id_pipeline_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name=op.f("fk_stage_runs_workspace_id_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("run_id", "stage", name=op.f("pk_stage_runs")),
    )
    op.create_index("ix_stage_runs_stage_finished_at", "stage_runs", ["stage", "finished_at"], unique=False)
    op.create_table(
        "url_inventory",
        sa.Column("website_id", sa.UUID(), nullable=False),
        sa.Column("url_hash", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("template_key", sa.Text(), nullable=True),
        sa.Column("page_type_guess", sa.Text(), nullable=True),
        sa.Column("first_seen", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("last_seen", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("sitemap_lastmod", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["website_id"],
            ["websites.id"],
            name=op.f("fk_url_inventory_website_id_websites"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name=op.f("fk_url_inventory_workspace_id_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("website_id", "url_hash", name=op.f("pk_url_inventory")),
    )
    op.create_index(
        "ix_url_inventory_website_id_template_key",
        "url_inventory",
        ["website_id", "template_key"],
        unique=False,
    )
    op.create_table(
        "business_profiles",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("parent_version", sa.Integer(), nullable=True),
        sa.Column("crawl_run_id", sa.UUID(), nullable=True),
        sa.Column("status", sa.Text(), server_default=sa.text("'draft'"), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("profile", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("profile_schema_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "coverage",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "quality",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "llm_usage",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "prompt_versions",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("generated_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "status IN ('draft', 'awaiting_confirmation', 'confirmed', 'superseded')",
            name=op.f("ck_business_profiles_status"),
        ),
        sa.ForeignKeyConstraint(
            ["crawl_run_id"],
            ["crawl_runs.id"],
            name=op.f("fk_business_profiles_crawl_run_id_crawl_runs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name=op.f("fk_business_profiles_workspace_id_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_business_profiles")),
        sa.UniqueConstraint("client_id", "version", name=op.f("uq_business_profiles_client_id_version")),
    )
    op.create_index(
        "ix_business_profiles_client_id_confirmed",
        "business_profiles",
        ["client_id"],
        unique=False,
        postgresql_where=sa.text("status = 'confirmed'"),
    )
    op.create_table(
        "competitor_candidates",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("brand_name", sa.Text(), nullable=False),
        sa.Column("normalized_name", sa.Text(), nullable=False),
        sa.Column(
            "aliases", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False
        ),
        sa.Column("official_domain", sa.Text(), nullable=True),
        sa.Column("relation", sa.Text(), nullable=False),
        sa.Column("owner_kind", sa.Text(), server_default=sa.text("'unknown'"), nullable=False),
        sa.Column("role", sa.Text(), nullable=True),
        sa.Column(
            "geography",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "business",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("business_schema_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("confidence", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'suggested'"), nullable=False),
        sa.Column(
            "flags", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False
        ),
        sa.Column("evidence_summary", sa.Text(), nullable=True),
        sa.Column("decided_by", sa.Text(), nullable=True),
        sa.Column("decided_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("first_seen_run_id", sa.UUID(), nullable=True),
        sa.Column("last_seen_run_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "role IS NULL OR role IN ('competitor', 'niche', 'benchmark', 'creator')",
            name=op.f("ck_competitor_candidates_role"),
        ),
        sa.CheckConstraint(
            "status IN ('suggested', 'confirmed', 'auto_confirmed', 'rejected', 'unresolved', 'no_channel', 'excluded', 'user_added')",
            name=op.f("ck_competitor_candidates_status"),
        ),
        sa.ForeignKeyConstraint(
            ["first_seen_run_id"],
            ["competitor_discovery_runs.id"],
            name=op.f("fk_competitor_candidates_first_seen_run_id_competitor_discovery_runs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["last_seen_run_id"],
            ["competitor_discovery_runs.id"],
            name=op.f("fk_competitor_candidates_last_seen_run_id_competitor_discovery_runs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name=op.f("fk_competitor_candidates_workspace_id_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_competitor_candidates")),
        sa.UniqueConstraint(
            "client_id", "normalized_name", name=op.f("uq_competitor_candidates_client_id_normalized_name")
        ),
    )
    op.create_index(
        "ix_competitor_candidates_client_id_role_status",
        "competitor_candidates",
        ["client_id", "role", "status"],
        unique=False,
    )
    op.create_index(
        "uq_competitor_candidates_client_id_official_domain",
        "competitor_candidates",
        ["client_id", "official_domain"],
        unique=True,
        postgresql_where=sa.text("official_domain IS NOT NULL"),
    )
    op.create_table(
        "crawl_frontier",
        sa.Column("crawl_run_id", sa.UUID(), nullable=False),
        sa.Column("url_hash", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("depth", sa.SmallInteger(), nullable=False),
        sa.Column("page_type_guess", sa.Text(), nullable=True),
        sa.Column("template_key", sa.Text(), nullable=True),
        sa.Column("score", sa.REAL(), nullable=False),
        sa.Column("state", sa.Text(), server_default=sa.text("'queued'"), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("sitemap_lastmod", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["crawl_run_id"],
            ["crawl_runs.id"],
            name=op.f("fk_crawl_frontier_crawl_run_id_crawl_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name=op.f("fk_crawl_frontier_workspace_id_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("crawl_run_id", "url_hash", name=op.f("pk_crawl_frontier")),
    )
    op.create_table(
        "crawled_pages",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("website_id", sa.UUID(), nullable=False),
        sa.Column("url_hash", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=True),
        sa.Column("final_url", sa.Text(), nullable=True),
        sa.Column("status_code", sa.SmallInteger(), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=True),
        sa.Column("lang", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("meta_description", sa.Text(), nullable=True),
        sa.Column("h1", sa.Text(), nullable=True),
        sa.Column(
            "headings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.Text(), nullable=True),
        sa.Column("simhash", sa.BigInteger(), nullable=True),
        sa.Column(
            "json_ld",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "ctas",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "prices",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "links_internal",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "youtube_refs",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("page_type", sa.Text(), nullable=True),
        sa.Column("template_key", sa.Text(), nullable=True),
        sa.Column("fetch_mode", sa.Text(), nullable=False),
        sa.Column("source_kind", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("snapshot_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("etag", sa.Text(), nullable=True),
        sa.Column("last_modified", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("sitemap_lastmod", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("injection_flags", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("truncated", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("low_trust", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("fetched_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("last_crawl_run_id", sa.UUID(), nullable=True),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "source_kind IN ('site_direct', 'site_rendered', 'reader_proxy', 'search_index', 'archive', 'knowledge_base', 'web_mentions', 'youtube_channel', 'user')",
            name=op.f("ck_crawled_pages_source_kind"),
        ),
        sa.CheckConstraint("char_length(text) <= 60000", name=op.f("ck_crawled_pages_text_length")),
        sa.ForeignKeyConstraint(
            ["last_crawl_run_id"],
            ["crawl_runs.id"],
            name=op.f("fk_crawled_pages_last_crawl_run_id_crawl_runs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["website_id"],
            ["websites.id"],
            name=op.f("fk_crawled_pages_website_id_websites"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name=op.f("fk_crawled_pages_workspace_id_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_crawled_pages")),
        sa.UniqueConstraint("website_id", "url_hash", name=op.f("uq_crawled_pages_website_id_url_hash")),
    )
    op.create_index("ix_crawled_pages_content_hash", "crawled_pages", ["content_hash"], unique=False)
    op.create_index("ix_crawled_pages_fetched_at", "crawled_pages", ["fetched_at"], unique=False)
    op.create_index(
        "ix_crawled_pages_website_id_page_type", "crawled_pages", ["website_id", "page_type"], unique=False
    )
    op.create_table(
        "competitor_candidate_sources",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("candidate_id", sa.UUID(), nullable=False),
        sa.Column("discovery_run_id", sa.UUID(), nullable=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("ref", sa.Text(), nullable=False),
        sa.Column("snippet", sa.Text(), nullable=True),
        sa.Column("retrieved_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["competitor_candidates.id"],
            name=op.f("fk_competitor_candidate_sources_candidate_id_competitor_candidates"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["discovery_run_id"],
            ["competitor_discovery_runs.id"],
            name=op.f("fk_competitor_candidate_sources_discovery_run_id_competitor_discovery_runs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name=op.f("fk_competitor_candidate_sources_workspace_id_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_competitor_candidate_sources")),
    )
    op.create_index(
        "ix_competitor_candidate_sources_candidate_id",
        "competitor_candidate_sources",
        ["candidate_id"],
        unique=False,
    )
    op.create_index(
        "ix_competitor_candidate_sources_retrieved_at",
        "competitor_candidate_sources",
        ["retrieved_at"],
        unique=False,
    )
    op.create_table(
        "competitor_channel_matches",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("candidate_id", sa.UUID(), nullable=False),
        sa.Column("channel_id", sa.Text(), nullable=False),
        sa.Column("resolution_method", sa.Text(), nullable=False),
        sa.Column("variant", sa.Text(), server_default=sa.text("'unknown'"), nullable=False),
        sa.Column("is_primary", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("is_tracked", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("ownership_label", sa.Text(), nullable=False),
        sa.Column("ownership_score", sa.REAL(), nullable=False),
        sa.Column("ownership_method", sa.Text(), nullable=True),
        sa.Column(
            "ownership_signals",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("llm_reason", sa.Text(), nullable=True),
        sa.Column("youtube_evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "youtube_evidence_schema_version", sa.Integer(), server_default=sa.text("1"), nullable=False
        ),
        sa.Column("youtube_fetched_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "flags", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False
        ),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "ownership_label IN ('verified', 'likely', 'uncertain', 'rejected')",
            name=op.f("ck_competitor_channel_matches_ownership_label"),
        ),
        sa.CheckConstraint(
            "ownership_method IS NULL OR ownership_method IN ('oauth', 'link', 'user')",
            name=op.f("ck_competitor_channel_matches_ownership_method"),
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["competitor_candidates.id"],
            name=op.f("fk_competitor_channel_matches_candidate_id_competitor_candidates"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name=op.f("fk_competitor_channel_matches_workspace_id_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_competitor_channel_matches")),
        sa.UniqueConstraint(
            "candidate_id", "channel_id", name=op.f("uq_competitor_channel_matches_candidate_id_channel_id")
        ),
    )
    op.create_index(
        "ix_competitor_channel_matches_channel_id", "competitor_channel_matches", ["channel_id"], unique=False
    )
    op.create_index(
        "ix_competitor_channel_matches_youtube_fetched_at",
        "competitor_channel_matches",
        ["youtube_fetched_at"],
        unique=False,
    )
    op.create_table(
        "competitor_drift_events",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("candidate_id", sa.UUID(), nullable=True),
        sa.Column("channel_id", sa.Text(), nullable=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column(
            "detail",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("detected_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("acknowledged_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["competitor_candidates.id"],
            name=op.f("fk_competitor_drift_events_candidate_id_competitor_candidates"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name=op.f("fk_competitor_drift_events_workspace_id_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_competitor_drift_events")),
    )
    op.create_index(
        "ix_competitor_drift_events_client_id_acknowledged_at",
        "competitor_drift_events",
        [
            "client_id",
            sa.literal_column("acknowledged_at NULLS FIRST"),
            sa.literal_column("detected_at DESC"),
        ],
        unique=False,
    )
    op.create_table(
        "keywords",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("language", sa.Text(), nullable=False),
        sa.Column("script", sa.Text(), nullable=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("tier", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'proposed'"), nullable=False),
        sa.Column("priority", sa.SmallInteger(), server_default=sa.text("2"), nullable=False),
        sa.Column("weight", sa.REAL(), server_default=sa.text("1.0"), nullable=False),
        sa.Column(
            "offering_ids",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column(
            "meta",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("embedding", postgresql.BYTEA(), nullable=True),
        sa.Column("embedding_model", sa.Text(), nullable=True),
        sa.Column("uses_authorized_data", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("api_data_observed_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("introduced_in_profile_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "status IN ('proposed', 'confirmed', 'rejected', 'retired')", name=op.f("ck_keywords_status")
        ),
        sa.CheckConstraint("priority BETWEEN 1 AND 3", name=op.f("ck_keywords_priority")),
        sa.ForeignKeyConstraint(
            ["introduced_in_profile_id"],
            ["business_profiles.id"],
            name=op.f("fk_keywords_introduced_in_profile_id_business_profiles"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name=op.f("fk_keywords_workspace_id_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_keywords")),
        sa.UniqueConstraint(
            "client_id",
            "normalized_text",
            "language",
            name=op.f("uq_keywords_client_id_normalized_text_language"),
        ),
    )
    op.create_index(
        "ix_keywords_api_data_observed_at",
        "keywords",
        ["api_data_observed_at"],
        unique=False,
        postgresql_where=sa.text("api_data_observed_at IS NOT NULL"),
    )
    op.create_index("ix_keywords_client_id_status", "keywords", ["client_id", "status"], unique=False)
    op.create_table(
        "page_blobs",
        sa.Column("page_id", sa.UUID(), nullable=False),
        sa.Column("raw_html_gz", postgresql.BYTEA(), nullable=False),
        sa.Column("expires_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["page_id"],
            ["crawled_pages.id"],
            name=op.f("fk_page_blobs_page_id_crawled_pages"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name=op.f("fk_page_blobs_workspace_id_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("page_id", name=op.f("pk_page_blobs")),
    )
    op.create_index("ix_page_blobs_expires_at", "page_blobs", ["expires_at"], unique=False)
    op.create_table(
        "page_facts",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("page_id", sa.UUID(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("extractor_version", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column(
            "facts",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("facts_schema_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("verified_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("dropped_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("source_kind", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("snapshot_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("fetched_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("superseded_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "source_kind IN ('site_direct', 'site_rendered', 'reader_proxy', 'search_index', 'archive', 'knowledge_base', 'web_mentions', 'youtube_channel', 'user')",
            name=op.f("ck_page_facts_source_kind"),
        ),
        sa.ForeignKeyConstraint(
            ["page_id"],
            ["crawled_pages.id"],
            name=op.f("fk_page_facts_page_id_crawled_pages"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name=op.f("fk_page_facts_workspace_id_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_page_facts")),
        sa.UniqueConstraint(
            "client_id",
            "content_hash",
            "extractor_version",
            "prompt_version",
            "model",
            name=op.f("uq_page_facts_client_id_content_hash_extractor_version_prompt_version_model"),
        ),
    )
    op.create_index("ix_page_facts_page_id", "page_facts", ["page_id"], unique=False)
    op.create_index(
        "ix_page_facts_superseded_at",
        "page_facts",
        ["superseded_at"],
        unique=False,
        postgresql_where=sa.text("superseded_at IS NOT NULL"),
    )
    op.create_foreign_key(
        op.f("fk_websites_last_crawl_run_id_crawl_runs"),
        "websites",
        "crawl_runs",
        ["last_crawl_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.execute(TRACKED_CHANNELS_VIEW_SQL)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS v_tracked_channels")
    op.drop_constraint(op.f("fk_websites_last_crawl_run_id_crawl_runs"), "websites", type_="foreignkey")
    op.drop_table("page_facts")
    op.drop_table("page_blobs")
    op.drop_table("keywords")
    op.drop_table("competitor_drift_events")
    op.drop_table("competitor_channel_matches")
    op.drop_table("competitor_candidate_sources")
    op.drop_table("crawled_pages")
    op.drop_table("crawl_frontier")
    op.drop_table("competitor_candidates")
    op.drop_table("business_profiles")
    op.drop_table("url_inventory")
    op.drop_table("stage_runs")
    op.drop_table("run_events")
    op.drop_table("run_artifacts")
    op.drop_table("own_traffic_source")
    op.drop_table("own_search_term")
    op.drop_table("own_analytics_daily")
    op.drop_table("crawl_runs")
    op.drop_table("competitor_discovery_runs")
    op.drop_table("client_schedules")
    op.drop_table("active_result_sets")
    op.drop_table("websites")
    op.drop_table("profile_tombstones")
    op.drop_table("profile_edits")
    op.drop_table("profile_diffs")
    op.drop_table("pipeline_runs")
    op.drop_table("competitor_sets")
    op.drop_table("buyer_questions")
