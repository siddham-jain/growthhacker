"""extensions, tenancy, auth, clients and oauth tokens

Revision ID: 0001
Revises:
Create Date: 2026-11-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=True),
        sa.Column("actor_kind", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.UUID(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("target_kind", sa.Text(), nullable=True),
        sa.Column("target_id", sa.Text(), nullable=True),
        sa.Column(
            "data",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("ip_prefix", postgresql.INET(), nullable=True),
        sa.Column("at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
    )
    op.create_index("ix_audit_log_at_brin", "audit_log", ["at"], unique=False, postgresql_using="brin")
    op.create_index(
        "ix_audit_log_workspace_id_at",
        "audit_log",
        ["workspace_id", sa.literal_column("at DESC")],
        unique=False,
    )
    op.create_table(
        "deletion_requests",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=True),
        sa.Column("client_id", sa.UUID(), nullable=True),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("oauth_token_id", sa.UUID(), nullable=True),
        sa.Column("requested_by", sa.Text(), nullable=False),
        sa.Column("requested_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("due_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("started_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("completed_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column(
            "report",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "scope IN ('oauth_revocation', 'client', 'workspace', 'user', 'api_data_request')",
            name=op.f("ck_deletion_requests_scope"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'done', 'failed')", name=op.f("ck_deletion_requests_status")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_deletion_requests")),
    )
    op.create_index(
        "ix_deletion_requests_status_due_at", "deletion_requests", ["status", "due_at"], unique=False
    )
    op.create_table(
        "users",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("email", postgresql.CITEXT(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("password_hash", sa.Text(), nullable=True),
        sa.Column("email_verified_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_login_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("disabled_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )
    op.create_table(
        "workspaces",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("slug", postgresql.CITEXT(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'active'"), nullable=False),
        sa.Column("plan", sa.Text(), server_default=sa.text("'standard'"), nullable=False),
        sa.Column(
            "settings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("settings_schema_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("deleted_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("purge_after", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('active', 'suspended', 'deleting')", name=op.f("ck_workspaces_status")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspaces")),
        sa.UniqueConstraint("slug", name=op.f("uq_workspaces_slug")),
    )
    op.create_table(
        "api_keys",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("environment", sa.Text(), nullable=False),
        sa.Column("key_prefix", sa.Text(), nullable=False),
        sa.Column("secret_hash", postgresql.BYTEA(), nullable=False),
        sa.Column("scopes", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("last_used_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("expires_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("revoked_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_api_keys_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_api_keys_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_keys")),
        sa.UniqueConstraint("key_prefix", name=op.f("uq_api_keys_key_prefix")),
    )
    op.create_index(op.f("ix_api_keys_workspace_id"), "api_keys", ["workspace_id"], unique=False)
    op.create_table(
        "clients",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("slug", postgresql.CITEXT(), nullable=False),
        sa.Column("website_url", sa.Text(), nullable=True),
        sa.Column("website_domain", postgresql.CITEXT(), nullable=True),
        sa.Column("own_channel_id", sa.Text(), nullable=True),
        sa.Column("own_channel_status", sa.Text(), server_default=sa.text("'unresolved'"), nullable=False),
        sa.Column(
            "own_channel_verification",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("timezone", sa.Text(), server_default=sa.text("'UTC'"), nullable=False),
        sa.Column("country", sa.Text(), nullable=True),
        sa.Column("language", sa.Text(), nullable=True),
        sa.Column("lifecycle", sa.Text(), server_default=sa.text("'created'"), nullable=False),
        sa.Column(
            "settings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("settings_schema_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("next_refresh_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_activity_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("archived_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("deleted_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "lifecycle IN ('created', 'onboarding', 'onboarding_failed', 'awaiting_confirmation', 'analyzing', 'active', 'paused', 'deleting', 'archived')",
            name=op.f("ck_clients_lifecycle"),
        ),
        sa.CheckConstraint(
            "own_channel_status IN ('unresolved', 'proposed', 'verified', 'rejected', 'needs_attention')",
            name=op.f("ck_clients_own_channel_status"),
        ),
        sa.CheckConstraint(
            "website_url IS NOT NULL OR own_channel_id IS NOT NULL",
            name=op.f("ck_clients_has_website_or_channel"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_clients_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_clients")),
        sa.UniqueConstraint("workspace_id", "id", name=op.f("uq_clients_workspace_id_id")),
        sa.UniqueConstraint("workspace_id", "slug", name=op.f("uq_clients_workspace_id_slug")),
    )
    op.create_index(
        "ix_clients_next_refresh_at",
        "clients",
        ["next_refresh_at"],
        unique=False,
        postgresql_where=sa.text("lifecycle = 'active'"),
    )
    op.create_index(
        "uq_clients_workspace_id_own_channel_id",
        "clients",
        ["workspace_id", "own_channel_id"],
        unique=True,
        postgresql_where=sa.text("own_channel_id IS NOT NULL AND deleted_at IS NULL"),
    )
    op.create_table(
        "idempotency_keys",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("fingerprint", sa.Text(), nullable=False),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("response_status", sa.SmallInteger(), nullable=True),
        sa.Column("response_headers", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("response_body", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("resource_id", sa.Text(), nullable=True),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("expires_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_idempotency_keys_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("workspace_id", "key", name=op.f("pk_idempotency_keys")),
    )
    op.create_index(op.f("ix_idempotency_keys_expires_at"), "idempotency_keys", ["expires_at"], unique=False)
    op.create_table(
        "user_sessions",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("active_workspace_id", sa.UUID(), nullable=True),
        sa.Column("token_hash", postgresql.BYTEA(), nullable=False),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("last_seen_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("expires_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("idle_expires_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("revoked_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("ip_prefix", postgresql.INET(), nullable=True),
        sa.ForeignKeyConstraint(
            ["active_workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_user_sessions_active_workspace_id_workspaces"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_user_sessions_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_sessions")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_user_sessions_token_hash")),
    )
    op.create_index(op.f("ix_user_sessions_expires_at"), "user_sessions", ["expires_at"], unique=False)
    op.create_index(op.f("ix_user_sessions_user_id"), "user_sessions", ["user_id"], unique=False)
    op.create_table(
        "workspace_members",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint("role IN ('owner', 'editor', 'viewer')", name=op.f("ck_workspace_members_role")),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_workspace_members_user_id_users"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_workspace_members_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("workspace_id", "user_id", name=op.f("pk_workspace_members")),
    )
    op.create_index(op.f("ix_workspace_members_user_id"), "workspace_members", ["user_id"], unique=False)
    op.create_table(
        "oauth_tokens",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("provider", sa.Text(), server_default=sa.text("'google'"), nullable=False),
        sa.Column("google_subject", sa.Text(), nullable=False),
        sa.Column("channel_id", sa.Text(), nullable=False),
        sa.Column("scopes", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("key_id", sa.Text(), nullable=False),
        sa.Column("encrypted_payload", postgresql.BYTEA(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'active'"), nullable=False),
        sa.Column("approved_viewer_user_ids", postgresql.ARRAY(sa.UUID()), nullable=False),
        sa.Column("granted_by_user_id", sa.UUID(), nullable=False),
        sa.Column("granted_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("last_refreshed_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("authorization_verified_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("failure_count", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("revoked_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("revocation_source", sa.Text(), nullable=True),
        sa.Column("purge_due_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.CheckConstraint("provider IN ('google')", name=op.f("ck_oauth_tokens_provider")),
        sa.CheckConstraint(
            "revocation_source IS NULL OR revocation_source IN ('in_app', 'google_risc', 'invalid_grant', 'client_deleted')",
            name=op.f("ck_oauth_tokens_revocation_source"),
        ),
        sa.CheckConstraint(
            "status IN ('active', 'invalid', 'revoked', 'purged')", name=op.f("ck_oauth_tokens_status")
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name=op.f("fk_oauth_tokens_workspace_id_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_oauth_tokens")),
    )
    op.create_index(
        "ix_oauth_tokens_authorization_verified_at",
        "oauth_tokens",
        ["authorization_verified_at"],
        unique=False,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(op.f("ix_oauth_tokens_key_id"), "oauth_tokens", ["key_id"], unique=False)
    op.create_index(
        "uq_oauth_tokens_client_id_provider_active",
        "oauth_tokens",
        ["client_id", "provider"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_table("oauth_tokens")
    op.drop_table("workspace_members")
    op.drop_table("user_sessions")
    op.drop_table("idempotency_keys")
    op.drop_table("clients")
    op.drop_table("api_keys")
    op.drop_table("workspaces")
    op.drop_table("users")
    op.drop_table("deletion_requests")
    op.drop_table("audit_log")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
    op.execute("DROP EXTENSION IF EXISTS citext")
