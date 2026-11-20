"""shared youtube cache with daily snapshot partitions

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PARTITIONED_TABLES = (("yt_video_snapshots", "slot_at"), ("yt_channel_snapshots", "captured_at"))

# later partitions come from app.retention.partitions.ensure_partitions via the retention cron
INITIAL_PARTITIONS_SQL = """
DO $$
DECLARE
  day date;
BEGIN
  FOR day IN SELECT generate_series(current_date - 1, current_date + 7, interval '1 day')::date LOOP
    EXECUTE format(
      'CREATE TABLE IF NOT EXISTS %I PARTITION OF {table} FOR VALUES FROM (%L) TO (%L)',
      '{table}_p' || to_char(day, 'YYYYMMDD'),
      day::timestamp AT TIME ZONE 'UTC',
      (day + 1)::timestamp AT TIME ZONE 'UTC'
    );
  END LOOP;
END $$
"""


def upgrade() -> None:
    op.create_table(
        "brand_channel_resolutions",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("key_type", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("channel_id", sa.Text(), nullable=False),
        sa.Column("ownership_label", sa.Text(), nullable=False),
        sa.Column("ownership_score", sa.REAL(), nullable=False),
        sa.Column("ownership_method", sa.Text(), nullable=True),
        sa.Column(
            "signals",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("verified_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("expires_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.CheckConstraint(
            "ownership_label IN ('verified', 'likely', 'uncertain', 'rejected')",
            name=op.f("ck_brand_channel_resolutions_ownership_label"),
        ),
        sa.CheckConstraint(
            "ownership_method IS NULL OR ownership_method IN ('oauth', 'link', 'user')",
            name=op.f("ck_brand_channel_resolutions_ownership_method"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_brand_channel_resolutions")),
        sa.UniqueConstraint(
            "key_type", "key", "channel_id", name=op.f("uq_brand_channel_resolutions_key_type_key_channel_id")
        ),
    )
    op.create_index(
        "ix_brand_channel_resolutions_expires_at", "brand_channel_resolutions", ["expires_at"], unique=False
    )
    op.create_table(
        "creator_collaborations",
        sa.Column("creator_channel_id", sa.Text(), nullable=False),
        sa.Column("other_channel_id", sa.Text(), nullable=False),
        sa.Column("video_id", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("matched_on", sa.Text(), nullable=False),
        sa.Column("matched_token", sa.Text(), nullable=False),
        sa.Column("video_published_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "detected_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint(
            "creator_channel_id",
            "other_channel_id",
            "video_id",
            "kind",
            name=op.f("pk_creator_collaborations"),
        ),
    )
    op.create_index(
        "ix_creator_collaborations_detected_at", "creator_collaborations", ["detected_at"], unique=False
    )
    op.create_index(
        op.f("ix_creator_collaborations_other_channel_id"),
        "creator_collaborations",
        ["other_channel_id"],
        unique=False,
    )
    op.create_table(
        "yt_channel_resolutions",
        sa.Column("input_kind", sa.Text(), nullable=False),
        sa.Column("input_normalized", sa.Text(), nullable=False),
        sa.Column("channel_id", sa.Text(), nullable=True),
        sa.Column("resolved_via", sa.Text(), nullable=False),
        sa.Column("resolved_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("expires_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.CheckConstraint(
            "expires_at <= resolved_at + interval '28 days'",
            name=op.f("ck_yt_channel_resolutions_expires_within_28d"),
        ),
        sa.CheckConstraint(
            "input_kind IN ('handle', 'username', 'custom_url', 'channel_url', 'video_url', 'channel_id')",
            name=op.f("ck_yt_channel_resolutions_input_kind"),
        ),
        sa.PrimaryKeyConstraint("input_kind", "input_normalized", name=op.f("pk_yt_channel_resolutions")),
    )
    op.create_index(
        op.f("ix_yt_channel_resolutions_expires_at"), "yt_channel_resolutions", ["expires_at"], unique=False
    )
    op.create_table(
        "yt_channels",
        sa.Column("channel_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("handle", postgresql.CITEXT(), nullable=True),
        sa.Column("custom_url", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("country", sa.Text(), nullable=True),
        sa.Column("default_language", sa.Text(), nullable=True),
        sa.Column("published_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("uploads_playlist_id", sa.Text(), nullable=True),
        sa.Column("subscriber_count", sa.BigInteger(), nullable=True),
        sa.Column("hidden_subscriber_count", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("view_count", sa.BigInteger(), nullable=True),
        sa.Column("video_count", sa.Integer(), nullable=True),
        sa.Column(
            "keywords", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False
        ),
        sa.Column("thumbnail_url", sa.Text(), nullable=True),
        sa.Column("made_for_kids", sa.Boolean(), nullable=True),
        sa.Column("availability", sa.Text(), server_default=sa.text("'available'"), nullable=False),
        sa.Column("unavailable_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("uploads_90d", sa.Integer(), nullable=True),
        sa.Column("last_upload_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "recent_upload_ids",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column(
            "cadence_flags",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("etag", sa.Text(), nullable=True),
        sa.Column("metadata_fetched_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("stats_fetched_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("uploads_synced_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("uploads_synced_through", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("backfill_cursor", sa.Text(), nullable=True),
        sa.Column("backfill_horizon", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("backfill_complete", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "first_seen_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "availability IN ('available', 'unavailable')", name=op.f("ck_yt_channels_availability")
        ),
        sa.CheckConstraint(
            "channel_id ~ '^UC[0-9A-Za-z_-]{22}$'", name=op.f("ck_yt_channels_channel_id_format")
        ),
        sa.PrimaryKeyConstraint("channel_id", name=op.f("pk_yt_channels")),
    )
    op.create_index("ix_yt_channels_handle", "yt_channels", ["handle"], unique=False)
    op.create_index(
        "ix_yt_channels_retention_clock",
        "yt_channels",
        [sa.literal_column("least(metadata_fetched_at, stats_fetched_at)")],
        unique=False,
    )
    op.create_index("ix_yt_channels_uploads_synced_at", "yt_channels", ["uploads_synced_at"], unique=False)
    op.create_table(
        "yt_demand_checks",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("entity_norm", sa.Text(), nullable=False),
        sa.Column("day_bucket", sa.Date(), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("published_after", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "video_ids", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False
        ),
        sa.Column(
            "early_videos",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "aggregates",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("quota_units", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("checked_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_yt_demand_checks")),
        sa.UniqueConstraint(
            "entity_norm", "day_bucket", name=op.f("uq_yt_demand_checks_entity_norm_day_bucket")
        ),
    )
    op.create_index(op.f("ix_yt_demand_checks_checked_at"), "yt_demand_checks", ["checked_at"], unique=False)
    op.create_table(
        "yt_http_etag",
        sa.Column("request_key", sa.Text(), nullable=False),
        sa.Column("etag", sa.Text(), nullable=False),
        sa.Column("stored_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("request_key", name=op.f("pk_yt_http_etag")),
    )
    op.create_index(op.f("ix_yt_http_etag_stored_at"), "yt_http_etag", ["stored_at"], unique=False)
    op.create_table(
        "yt_search_cache",
        sa.Column("query_key", sa.Text(), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("params", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("total_results", sa.Integer(), nullable=True),
        sa.Column("next_page_token", sa.Text(), nullable=True),
        sa.Column("quota_units", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("fetched_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("expires_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("hit_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_hit_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "expires_at <= fetched_at + interval '28 days'",
            name=op.f("ck_yt_search_cache_expires_within_28d"),
        ),
        sa.PrimaryKeyConstraint("query_key", name=op.f("pk_yt_search_cache")),
    )
    op.create_index(op.f("ix_yt_search_cache_expires_at"), "yt_search_cache", ["expires_at"], unique=False)
    op.create_table(
        "channel_boilerplate",
        sa.Column("channel_id", sa.Text(), nullable=False),
        sa.Column("boilerplate_version", sa.Text(), nullable=False),
        sa.Column(
            "line_keys",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "hashtags", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False
        ),
        sa.Column(
            "tags", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False
        ),
        sa.Column("sample_size", sa.Integer(), nullable=False),
        sa.Column("computed_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["yt_channels.channel_id"],
            name=op.f("fk_channel_boilerplate_channel_id_yt_channels"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("channel_id", name=op.f("pk_channel_boilerplate")),
    )
    op.create_index(
        "ix_channel_boilerplate_computed_at", "channel_boilerplate", ["computed_at"], unique=False
    )
    op.create_table(
        "yt_channel_recurring_text",
        sa.Column("channel_id", sa.Text(), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("grid_cell", sa.SmallInteger(), nullable=False),
        sa.Column("share", sa.REAL(), nullable=False),
        sa.Column("as_of", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("source_oldest_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["yt_channels.channel_id"],
            name=op.f("fk_yt_channel_recurring_text_channel_id_yt_channels"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "channel_id", "normalized_text", "grid_cell", name=op.f("pk_yt_channel_recurring_text")
        ),
    )
    op.create_index(
        op.f("ix_yt_channel_recurring_text_source_oldest_at"),
        "yt_channel_recurring_text",
        ["source_oldest_at"],
        unique=False,
    )
    op.create_table(
        "yt_channel_snapshots",
        sa.Column("channel_id", sa.Text(), nullable=False),
        sa.Column("captured_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("subscriber_count", sa.BigInteger(), nullable=True),
        sa.Column("view_count", sa.BigInteger(), nullable=True),
        sa.Column("video_count", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["yt_channels.channel_id"],
            name=op.f("fk_yt_channel_snapshots_channel_id_yt_channels"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("channel_id", "captured_at", name=op.f("pk_yt_channel_snapshots")),
        postgresql_partition_by="RANGE (captured_at)",
    )
    op.create_table(
        "yt_search_result",
        sa.Column("query_key", sa.Text(), nullable=False),
        sa.Column("rank", sa.SmallInteger(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("resource_id", sa.Text(), nullable=False),
        sa.Column("channel_id", sa.Text(), nullable=True),
        sa.CheckConstraint("kind IN ('video', 'channel', 'playlist')", name=op.f("ck_yt_search_result_kind")),
        sa.ForeignKeyConstraint(
            ["query_key"],
            ["yt_search_cache.query_key"],
            name=op.f("fk_yt_search_result_query_key_yt_search_cache"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("query_key", "rank", name=op.f("pk_yt_search_result")),
    )
    op.create_index(
        op.f("ix_yt_search_result_resource_id"), "yt_search_result", ["resource_id"], unique=False
    )
    op.create_table(
        "yt_videos",
        sa.Column("video_id", sa.Text(), nullable=False),
        sa.Column("channel_id", sa.Text(), nullable=False),
        sa.Column("channel_title", sa.Text(), nullable=True),
        sa.Column("published_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("effective_published_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("live_broadcast_content", sa.Text(), nullable=True),
        sa.Column("live_actual_start_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("live_actual_end_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("live_scheduled_start_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "tags", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False
        ),
        sa.Column("content_hash", sa.Text(), nullable=True),
        sa.Column("description_hash", sa.Text(), nullable=True),
        sa.Column("default_language", sa.Text(), nullable=True),
        sa.Column("default_audio_language", sa.Text(), nullable=True),
        sa.Column(
            "localizations",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("duration_s", sa.Integer(), nullable=True),
        sa.Column("embed_width", sa.Integer(), nullable=True),
        sa.Column("embed_height", sa.Integer(), nullable=True),
        sa.Column("aspect", sa.REAL(), nullable=True),
        sa.Column("format", sa.Text(), server_default=sa.text("'unknown'"), nullable=False),
        sa.Column(
            "format_basis",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("format_confidence", sa.REAL(), nullable=True),
        sa.Column("has_captions", sa.Boolean(), nullable=True),
        sa.Column("licensed_content", sa.Boolean(), nullable=True),
        sa.Column("age_restricted", sa.Boolean(), nullable=True),
        sa.Column("region_restriction", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("made_for_kids", sa.Boolean(), nullable=True),
        sa.Column("privacy_status", sa.Text(), nullable=True),
        sa.Column("upload_status", sa.Text(), nullable=True),
        sa.Column("embeddable", sa.Boolean(), nullable=True),
        sa.Column("public_stats_viewable", sa.Boolean(), nullable=True),
        sa.Column("has_paid_product_placement", sa.Boolean(), nullable=True),
        sa.Column(
            "thumbnails",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("thumbnail_url", sa.Text(), nullable=True),
        sa.Column("comments_state", sa.Text(), server_default=sa.text("'unknown'"), nullable=False),
        sa.Column("likes_visible", sa.Boolean(), nullable=True),
        sa.Column("view_count", sa.BigInteger(), nullable=True),
        sa.Column("like_count", sa.BigInteger(), nullable=True),
        sa.Column("comment_count", sa.BigInteger(), nullable=True),
        sa.Column("availability", sa.Text(), server_default=sa.text("'available'"), nullable=False),
        sa.Column("first_missing_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("ingest_source", sa.Text(), nullable=False),
        sa.Column(
            "quality_flags",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("etag", sa.Text(), nullable=True),
        sa.Column("metadata_fetched_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("stats_fetched_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "first_seen_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "availability IN ('available', 'missing', 'removed')", name=op.f("ck_yt_videos_availability")
        ),
        sa.CheckConstraint(
            "comments_state IN ('enabled', 'disabled', 'unknown')", name=op.f("ck_yt_videos_comments_state")
        ),
        sa.CheckConstraint(
            "format IN ('short_form', 'long_form', 'live', 'upcoming', 'unknown')",
            name=op.f("ck_yt_videos_format"),
        ),
        sa.CheckConstraint(
            "ingest_source IN ('uploads', 'search', 'manual', 'recommendation_ref')",
            name=op.f("ck_yt_videos_ingest_source"),
        ),
        sa.CheckConstraint(
            "live_broadcast_content IS NULL OR live_broadcast_content IN ('none', 'live', 'upcoming')",
            name=op.f("ck_yt_videos_live_broadcast_content"),
        ),
        sa.CheckConstraint("video_id ~ '^[0-9A-Za-z_-]{11}$'", name=op.f("ck_yt_videos_video_id_format")),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["yt_channels.channel_id"],
            name=op.f("fk_yt_videos_channel_id_yt_channels"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("video_id", name=op.f("pk_yt_videos")),
    )
    op.create_index(
        "ix_yt_videos_availability",
        "yt_videos",
        ["availability"],
        unique=False,
        postgresql_where=sa.text("availability <> 'available'"),
    )
    op.create_index(
        "ix_yt_videos_channel_id_effective_published_at",
        "yt_videos",
        ["channel_id", sa.literal_column("effective_published_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_yt_videos_format",
        "yt_videos",
        ["format"],
        unique=False,
        postgresql_where=sa.text("availability = 'available'"),
    )
    op.create_index(
        "ix_yt_videos_published_at", "yt_videos", [sa.literal_column("published_at DESC")], unique=False
    )
    op.create_index(
        "ix_yt_videos_retention_clock",
        "yt_videos",
        [sa.literal_column("least(metadata_fetched_at, stats_fetched_at)")],
        unique=False,
    )
    op.create_table(
        "text_embeddings",
        sa.Column("model_id", sa.Text(), nullable=False),
        sa.Column("text_hash", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("dim", sa.SmallInteger(), nullable=False),
        sa.Column("vector", postgresql.BYTEA(), nullable=False),
        sa.Column("source_video_id", sa.Text(), nullable=True),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["source_video_id"],
            ["yt_videos.video_id"],
            name=op.f("fk_text_embeddings_source_video_id_yt_videos"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("model_id", "text_hash", "role", name=op.f("pk_text_embeddings")),
    )
    op.create_index("ix_text_embeddings_created_at", "text_embeddings", ["created_at"], unique=False)
    op.create_index(
        "ix_text_embeddings_source_video_id", "text_embeddings", ["source_video_id"], unique=False
    )
    op.create_table(
        "video_prepared_text",
        sa.Column("video_id", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("prepared_hash", sa.Text(), nullable=False),
        sa.Column("boilerplate_version", sa.Text(), nullable=True),
        sa.Column("preparer_version", sa.Text(), nullable=False),
        sa.Column(
            "fields",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "removed_spans",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "script_profile",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("code_mixed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("lang_hint", sa.Text(), nullable=True),
        sa.Column(
            "quality_flags",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("prepared_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["video_id"],
            ["yt_videos.video_id"],
            name=op.f("fk_video_prepared_text_video_id_yt_videos"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("video_id", name=op.f("pk_video_prepared_text")),
    )
    op.create_index(
        "ix_video_prepared_text_prepared_at", "video_prepared_text", ["prepared_at"], unique=False
    )
    op.create_index(
        "ix_video_prepared_text_prepared_hash", "video_prepared_text", ["prepared_hash"], unique=False
    )
    op.create_table(
        "yt_channel_baselines",
        sa.Column("baseline_id", sa.Text(), nullable=False),
        sa.Column("channel_id", sa.Text(), nullable=False),
        sa.Column("segment", sa.Text(), nullable=False),
        sa.Column("metric", sa.Text(), nullable=False),
        sa.Column("age_days", sa.SmallInteger(), nullable=True),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("age_band", postgresql.NUMRANGE(), nullable=True),
        sa.Column("anchor_video_id", sa.Text(), nullable=True),
        sa.Column("as_of", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("median", sa.REAL(), nullable=False),
        sa.Column("mean", sa.REAL(), nullable=False),
        sa.Column("trimmed_mean_10", sa.REAL(), nullable=False),
        sa.Column("p25", sa.REAL(), nullable=False),
        sa.Column("p75", sa.REAL(), nullable=False),
        sa.Column("mad_log10", sa.REAL(), nullable=False),
        sa.Column("n", sa.Integer(), nullable=False),
        sa.Column(
            "video_ids", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False
        ),
        sa.Column("fallback_level", sa.Text(), nullable=False),
        sa.Column("sufficient", sa.Boolean(), nullable=False),
        sa.Column("source_oldest_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.CheckConstraint(
            "method IN ('observed', 'interpolated', 'owner_analytics', 'age_band_proxy', 'pending', 'unavailable')",
            name=op.f("ck_yt_channel_baselines_method"),
        ),
        sa.CheckConstraint(
            "segment IN ('short_form', 'long_form', 'live')", name=op.f("ck_yt_channel_baselines_segment")
        ),
        sa.ForeignKeyConstraint(
            ["anchor_video_id"],
            ["yt_videos.video_id"],
            name=op.f("fk_yt_channel_baselines_anchor_video_id_yt_videos"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["yt_channels.channel_id"],
            name=op.f("fk_yt_channel_baselines_channel_id_yt_channels"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("baseline_id", name=op.f("pk_yt_channel_baselines")),
    )
    op.create_index(
        op.f("ix_yt_channel_baselines_anchor_video_id"),
        "yt_channel_baselines",
        ["anchor_video_id"],
        unique=False,
    )
    op.create_index(
        "ix_yt_channel_baselines_channel_id_segment_metric_as_of",
        "yt_channel_baselines",
        ["channel_id", "segment", "metric", "age_days", "method", sa.literal_column("as_of DESC")],
        unique=False,
    )
    op.create_index(
        op.f("ix_yt_channel_baselines_source_oldest_at"),
        "yt_channel_baselines",
        ["source_oldest_at"],
        unique=False,
    )
    op.create_table(
        "yt_comment_fetch",
        sa.Column("video_id", sa.Text(), nullable=False),
        sa.Column("fetched_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("n", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.CheckConstraint(
            "outcome IN ('ok', 'disabled', 'empty', 'error')", name=op.f("ck_yt_comment_fetch_outcome")
        ),
        sa.ForeignKeyConstraint(
            ["video_id"],
            ["yt_videos.video_id"],
            name=op.f("fk_yt_comment_fetch_video_id_yt_videos"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("video_id", name=op.f("pk_yt_comment_fetch")),
    )
    op.create_index(op.f("ix_yt_comment_fetch_fetched_at"), "yt_comment_fetch", ["fetched_at"], unique=False)
    op.create_table(
        "yt_comment_signals",
        sa.Column("video_id", sa.Text(), nullable=False),
        sa.Column("as_of", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("comment_count_api", sa.BigInteger(), nullable=True),
        sa.Column("sampled_n", sa.Integer(), nullable=False),
        sa.Column("eligible_n", sa.Integer(), nullable=False),
        sa.Column("sample_coverage", sa.REAL(), nullable=True),
        sa.Column("question_n", sa.Integer(), nullable=False),
        sa.Column("question_share", sa.REAL(), nullable=True),
        sa.Column(
            "intent_counts",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("buyer_intent_n", sa.Integer(), nullable=False),
        sa.Column("buyer_intent_share", sa.REAL(), nullable=True),
        sa.Column("unanswered_question_n", sa.Integer(), nullable=False),
        sa.Column("unanswered_question_share", sa.REAL(), nullable=True),
        sa.Column("owner_reply_visible_n", sa.Integer(), nullable=False),
        sa.Column("lexicon_version", sa.Text(), nullable=False),
        sa.Column("sufficient", sa.Boolean(), nullable=False),
        sa.Column("source_oldest_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["video_id"],
            ["yt_videos.video_id"],
            name=op.f("fk_yt_comment_signals_video_id_yt_videos"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("video_id", name=op.f("pk_yt_comment_signals")),
    )
    op.create_index(
        op.f("ix_yt_comment_signals_source_oldest_at"),
        "yt_comment_signals",
        ["source_oldest_at"],
        unique=False,
    )
    op.create_table(
        "yt_comments",
        sa.Column("comment_id", sa.Text(), nullable=False),
        sa.Column("video_id", sa.Text(), nullable=False),
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("parent_comment_id", sa.Text(), nullable=True),
        sa.Column("is_reply", sa.Boolean(), nullable=False),
        sa.Column("text_redacted", sa.Text(), nullable=False),
        sa.Column("like_count", sa.Integer(), nullable=True),
        sa.Column("reply_count", sa.Integer(), nullable=True),
        sa.Column("relevance_rank", sa.Integer(), nullable=True),
        sa.Column("is_channel_owner_reply", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("published_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("source_updated_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("fetched_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.CheckConstraint(
            "char_length(text_redacted) <= 1500", name=op.f("ck_yt_comments_text_redacted_length")
        ),
        sa.ForeignKeyConstraint(
            ["video_id"],
            ["yt_videos.video_id"],
            name=op.f("fk_yt_comments_video_id_yt_videos"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("comment_id", name=op.f("pk_yt_comments")),
    )
    op.create_index(op.f("ix_yt_comments_fetched_at"), "yt_comments", ["fetched_at"], unique=False)
    op.create_index(
        "ix_yt_comments_video_id_published_at",
        "yt_comments",
        ["video_id", sa.literal_column("published_at DESC")],
        unique=False,
    )
    op.create_table(
        "yt_snapshot_schedule",
        sa.Column("video_id", sa.Text(), nullable=False),
        sa.Column("next_due_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("tier", sa.Text(), nullable=False),
        sa.Column("milestone_target_h", sa.Integer(), nullable=True),
        sa.Column("priority", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("tracking_started_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("first_seen_age_h", sa.REAL(), nullable=True),
        sa.Column("last_captured_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["video_id"],
            ["yt_videos.video_id"],
            name=op.f("fk_yt_snapshot_schedule_video_id_yt_videos"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("video_id", name=op.f("pk_yt_snapshot_schedule")),
    )
    op.create_index(
        op.f("ix_yt_snapshot_schedule_next_due_at"), "yt_snapshot_schedule", ["next_due_at"], unique=False
    )
    op.create_table(
        "yt_thumbnail_traits",
        sa.Column("video_id", sa.Text(), nullable=False),
        sa.Column("thumbnail_url", sa.Text(), nullable=False),
        sa.Column("image_etag", sa.Text(), nullable=True),
        sa.Column("source_key", sa.Text(), nullable=False),
        sa.Column("source_w", sa.SmallInteger(), nullable=False),
        sa.Column("source_h", sa.SmallInteger(), nullable=False),
        sa.Column("content_sha256", sa.Text(), nullable=False),
        sa.Column("letterbox_cropped", sa.Boolean(), nullable=False),
        sa.Column("pillarbox_detected", sa.Boolean(), nullable=False),
        sa.Column("text_present", sa.Boolean(), nullable=False),
        sa.Column("word_count", sa.SmallInteger(), nullable=True),
        sa.Column("word_count_band", sa.Text(), nullable=True),
        sa.Column("text_box_count", sa.SmallInteger(), nullable=False),
        sa.Column("text_area_ratio", sa.REAL(), nullable=False),
        sa.Column("largest_text_height_ratio", sa.REAL(), nullable=False),
        sa.Column("brand_text_only", sa.Boolean(), nullable=False),
        sa.Column("script_detected", sa.Text(), nullable=False),
        sa.Column("ocr_engine", sa.Text(), nullable=False),
        sa.Column("ocr_model", sa.Text(), nullable=False),
        sa.Column("ocr_confidence_mean", sa.REAL(), nullable=True),
        sa.Column("brightness", sa.REAL(), nullable=False),
        sa.Column("brightness_band", sa.Text(), nullable=False),
        sa.Column("rms_contrast", sa.REAL(), nullable=False),
        sa.Column("contrast_band", sa.Text(), nullable=False),
        sa.Column("colorfulness", sa.REAL(), nullable=False),
        sa.Column("colorfulness_band", sa.Text(), nullable=False),
        sa.Column("dominant_colour_count", sa.SmallInteger(), nullable=False),
        sa.Column("traits_version", sa.Text(), nullable=False),
        sa.Column("computed_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("source_verified_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["video_id"],
            ["yt_videos.video_id"],
            name=op.f("fk_yt_thumbnail_traits_video_id_yt_videos"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("video_id", name=op.f("pk_yt_thumbnail_traits")),
    )
    op.create_index(
        op.f("ix_yt_thumbnail_traits_content_sha256"), "yt_thumbnail_traits", ["content_sha256"], unique=False
    )
    op.create_index(
        op.f("ix_yt_thumbnail_traits_source_verified_at"),
        "yt_thumbnail_traits",
        ["source_verified_at"],
        unique=False,
    )
    op.create_table(
        "yt_video_age_values",
        sa.Column("video_id", sa.Text(), nullable=False),
        sa.Column("age_days", sa.SmallInteger(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("views", sa.BigInteger(), nullable=True),
        sa.Column("likes", sa.BigInteger(), nullable=True),
        sa.Column("comments", sa.BigInteger(), nullable=True),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("observed_age_days", sa.REAL(), nullable=True),
        sa.Column("bracket_lo_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("bracket_hi_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("bracket_spread", sa.REAL(), nullable=True),
        sa.Column("as_of", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("source_oldest_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.CheckConstraint(
            "method IN ('observed', 'interpolated', 'owner_analytics', 'age_band_proxy', 'pending', 'unavailable')",
            name=op.f("ck_yt_video_age_values_method"),
        ),
        sa.CheckConstraint(
            "source IN ('data_api_snapshot', 'analytics_api')", name=op.f("ck_yt_video_age_values_source")
        ),
        sa.ForeignKeyConstraint(
            ["video_id"],
            ["yt_videos.video_id"],
            name=op.f("fk_yt_video_age_values_video_id_yt_videos"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("video_id", "age_days", "source", name=op.f("pk_yt_video_age_values")),
    )
    op.create_index(
        op.f("ix_yt_video_age_values_source_oldest_at"),
        "yt_video_age_values",
        ["source_oldest_at"],
        unique=False,
    )
    op.create_table(
        "yt_video_metrics",
        sa.Column("video_id", sa.Text(), nullable=False),
        sa.Column("channel_id", sa.Text(), nullable=False),
        sa.Column("as_of", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("segment", sa.Text(), nullable=False),
        sa.Column(
            "segment_evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("format_key", sa.Text(), nullable=False),
        sa.Column("age_anchor_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("age_days", sa.REAL(), nullable=False),
        sa.Column("comparison_age_days", sa.SmallInteger(), nullable=True),
        sa.Column("views_now", sa.BigInteger(), nullable=False),
        sa.Column("likes_now", sa.BigInteger(), nullable=True),
        sa.Column("comments_now", sa.BigInteger(), nullable=True),
        sa.Column("views_per_day_lifetime", sa.REAL(), nullable=False),
        sa.Column("velocity_recent", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("velocity_7d", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("engagement_rate", sa.REAL(), nullable=True),
        sa.Column("like_rate", sa.REAL(), nullable=True),
        sa.Column("comment_rate", sa.REAL(), nullable=True),
        sa.Column("engagement_basis", sa.Text(), nullable=False),
        sa.Column("duplicate_group_id", sa.Text(), nullable=True),
        sa.Column(
            "flags", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False
        ),
        sa.Column("metrics_version", sa.Text(), nullable=False),
        sa.Column("source_oldest_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.CheckConstraint(
            "segment IN ('short_form', 'long_form', 'live')", name=op.f("ck_yt_video_metrics_segment")
        ),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["yt_channels.channel_id"],
            name=op.f("fk_yt_video_metrics_channel_id_yt_channels"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["video_id"],
            ["yt_videos.video_id"],
            name=op.f("fk_yt_video_metrics_video_id_yt_videos"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("video_id", name=op.f("pk_yt_video_metrics")),
    )
    op.create_index(
        "ix_yt_video_metrics_channel_id_segment", "yt_video_metrics", ["channel_id", "segment"], unique=False
    )
    op.create_index(
        op.f("ix_yt_video_metrics_source_oldest_at"), "yt_video_metrics", ["source_oldest_at"], unique=False
    )
    op.create_table(
        "yt_video_outliers",
        sa.Column("video_id", sa.Text(), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("age_days", sa.SmallInteger(), nullable=False),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("value", sa.BigInteger(), nullable=False),
        sa.Column("baseline_id", sa.Text(), nullable=False),
        sa.Column("baseline_median", sa.REAL(), nullable=False),
        sa.Column("baseline_n", sa.Integer(), nullable=False),
        sa.Column("ratio", sa.REAL(), nullable=False),
        sa.Column("log_robust_z", sa.REAL(), nullable=False),
        sa.Column("tier", sa.Text(), nullable=False),
        sa.Column("is_outlier", sa.Boolean(), nullable=False),
        sa.Column("suppressed_reason", sa.Text(), nullable=True),
        sa.Column("source_oldest_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.CheckConstraint(
            "method IN ('observed', 'interpolated', 'owner_analytics', 'age_band_proxy', 'pending', 'unavailable')",
            name=op.f("ck_yt_video_outliers_method"),
        ),
        sa.ForeignKeyConstraint(
            ["video_id"],
            ["yt_videos.video_id"],
            name=op.f("fk_yt_video_outliers_video_id_yt_videos"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "video_id", "as_of_date", "age_days", "method", name=op.f("pk_yt_video_outliers")
        ),
    )
    op.create_index(
        op.f("ix_yt_video_outliers_source_oldest_at"), "yt_video_outliers", ["source_oldest_at"], unique=False
    )
    op.create_index(
        "ix_yt_video_outliers_tier_as_of_date", "yt_video_outliers", ["tier", "as_of_date"], unique=False
    )
    op.create_table(
        "yt_video_snapshots",
        sa.Column("video_id", sa.Text(), nullable=False),
        sa.Column("slot_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("captured_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("age_hours", sa.REAL(), nullable=False),
        sa.Column("view_count", sa.BigInteger(), nullable=False),
        sa.Column("like_count", sa.BigInteger(), nullable=True),
        sa.Column("comment_count", sa.BigInteger(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("counting_regime", sa.Text(), server_default=sa.text("'standard'"), nullable=False),
        sa.CheckConstraint(
            "source IN ('batch_stats', 'videos_list', 'backtest_freeze')",
            name=op.f("ck_yt_video_snapshots_source"),
        ),
        sa.CheckConstraint("age_hours >= 0", name=op.f("ck_yt_video_snapshots_age_hours_non_negative")),
        sa.ForeignKeyConstraint(
            ["video_id"],
            ["yt_videos.video_id"],
            name=op.f("fk_yt_video_snapshots_video_id_yt_videos"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("video_id", "slot_at", name=op.f("pk_yt_video_snapshots")),
        postgresql_partition_by="RANGE (slot_at)",
    )
    op.create_index(
        "ix_yt_video_snapshots_video_id_age_hours",
        "yt_video_snapshots",
        ["video_id", "age_hours"],
        unique=False,
    )
    op.create_table(
        "yt_video_traits",
        sa.Column("video_id", sa.Text(), nullable=False),
        sa.Column(
            "traits",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("traits_schema_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("traits_version", sa.Text(), nullable=False),
        sa.Column("title_hash", sa.Text(), nullable=False),
        sa.Column("description_hash", sa.Text(), nullable=False),
        sa.Column("computed_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("source_oldest_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["video_id"],
            ["yt_videos.video_id"],
            name=op.f("fk_yt_video_traits_video_id_yt_videos"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("video_id", name=op.f("pk_yt_video_traits")),
    )
    op.create_index(
        op.f("ix_yt_video_traits_source_oldest_at"), "yt_video_traits", ["source_oldest_at"], unique=False
    )
    op.create_index(
        "ix_yt_video_traits_traits",
        "yt_video_traits",
        ["traits"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"traits": "jsonb_path_ops"},
    )
    for table, column in PARTITIONED_TABLES:
        op.execute(INITIAL_PARTITIONS_SQL.format(table=table, column=column))


def downgrade() -> None:
    op.drop_table("yt_video_traits")
    op.drop_table("yt_video_snapshots")
    op.drop_table("yt_video_outliers")
    op.drop_table("yt_video_metrics")
    op.drop_table("yt_video_age_values")
    op.drop_table("yt_thumbnail_traits")
    op.drop_table("yt_snapshot_schedule")
    op.drop_table("yt_comments")
    op.drop_table("yt_comment_signals")
    op.drop_table("yt_comment_fetch")
    op.drop_table("yt_channel_baselines")
    op.drop_table("video_prepared_text")
    op.drop_table("text_embeddings")
    op.drop_table("yt_videos")
    op.drop_table("yt_search_result")
    op.drop_table("yt_channel_snapshots")
    op.drop_table("yt_channel_recurring_text")
    op.drop_table("channel_boilerplate")
    op.drop_table("yt_search_cache")
    op.drop_table("yt_http_etag")
    op.drop_table("yt_demand_checks")
    op.drop_table("yt_channels")
    op.drop_table("yt_channel_resolutions")
    op.drop_table("creator_collaborations")
    op.drop_table("brand_channel_resolutions")
