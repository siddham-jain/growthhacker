import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    ForeignKey,
    Identity,
    Index,
    SmallInteger,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, CITEXT, INET, UUID
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
    updated_at_column,
    uuid_pk,
)

WORKSPACE_ROLES = ("owner", "editor", "viewer")
CLIENT_LIFECYCLES = (
    "created",
    "onboarding",
    "onboarding_failed",
    "awaiting_confirmation",
    "analyzing",
    "active",
    "paused",
    "deleting",
    "archived",
)
OWN_CHANNEL_STATUSES = ("unresolved", "proposed", "verified", "rejected", "needs_attention")


class Workspace(Base):
    __tablename__ = "workspaces"
    __zone__ = Zone.workspace
    __table_args__ = (check_in("status", ("active", "suspended", "deleting")),)

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str]
    slug: Mapped[str] = mapped_column(CITEXT(), unique=True)
    status: Mapped[str] = mapped_column(server_default=text("'active'"))
    plan: Mapped[str] = mapped_column(server_default=text("'standard'"))
    settings: Mapped[dict[str, Any]] = jsonb_object()
    settings_schema_version: Mapped[int] = schema_version_column()
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()
    deleted_at: Mapped[datetime | None]
    purge_after: Mapped[datetime | None]


class User(Base):
    __tablename__ = "users"
    __zone__ = Zone.global_

    id: Mapped[uuid.UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(CITEXT(), unique=True)
    display_name: Mapped[str | None]
    password_hash: Mapped[str | None]
    email_verified_at: Mapped[datetime | None]
    last_login_at: Mapped[datetime | None]
    disabled_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class WorkspaceMember(Base):
    __tablename__ = "workspace_members"
    __zone__ = Zone.workspace
    __table_args__ = (check_in("role", WORKSPACE_ROLES),)

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    role: Mapped[str]
    created_at: Mapped[datetime] = created_at_column()


class UserSession(Base):
    __tablename__ = "user_sessions"
    __zone__ = Zone.global_

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    active_workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL")
    )
    token_hash: Mapped[bytes] = mapped_column(unique=True)
    created_at: Mapped[datetime] = created_at_column()
    last_seen_at: Mapped[datetime]
    expires_at: Mapped[datetime] = mapped_column(index=True)
    idle_expires_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None]
    user_agent: Mapped[str | None]
    ip_prefix: Mapped[str | None] = mapped_column(INET())


class ApiKey(Base):
    __tablename__ = "api_keys"
    __zone__ = Zone.workspace

    id: Mapped[uuid.UUID] = uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str]
    environment: Mapped[str]
    key_prefix: Mapped[str] = mapped_column(unique=True)
    secret_hash: Mapped[bytes]
    scopes: Mapped[list[str]]
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = created_at_column()
    last_used_at: Mapped[datetime | None]
    expires_at: Mapped[datetime | None]
    revoked_at: Mapped[datetime | None]


class AuditLog(Base):
    __tablename__ = "audit_log"
    __zone__ = Zone.global_
    __table_args__ = (
        Index("ix_audit_log_workspace_id_at", "workspace_id", text("at DESC")),
        Index("ix_audit_log_at_brin", "at", postgresql_using="brin"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    workspace_id: Mapped[uuid.UUID | None]
    actor_kind: Mapped[str]
    actor_id: Mapped[uuid.UUID | None]
    action: Mapped[str]
    target_kind: Mapped[str | None]
    target_id: Mapped[str | None]
    data: Mapped[dict[str, Any]] = jsonb_object()
    ip_prefix: Mapped[str | None] = mapped_column(INET())
    at: Mapped[datetime] = created_at_column()


class DeletionRequest(Base):
    __tablename__ = "deletion_requests"
    __zone__ = Zone.global_
    __table_args__ = (
        check_in("scope", ("oauth_revocation", "client", "workspace", "user", "api_data_request")),
        check_in("status", ("pending", "running", "done", "failed")),
        Index("ix_deletion_requests_status_due_at", "status", "due_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    workspace_id: Mapped[uuid.UUID | None]
    client_id: Mapped[uuid.UUID | None]
    scope: Mapped[str]
    oauth_token_id: Mapped[uuid.UUID | None]
    requested_by: Mapped[str]
    requested_at: Mapped[datetime]
    due_at: Mapped[datetime]
    started_at: Mapped[datetime | None]
    completed_at: Mapped[datetime | None]
    status: Mapped[str] = mapped_column(server_default=text("'pending'"))
    report: Mapped[dict[str, Any]] = jsonb_object()


class Client(Base):
    __tablename__ = "clients"
    __zone__ = Zone.tenant
    __table_args__ = (
        UniqueConstraint("workspace_id", "id"),
        UniqueConstraint("workspace_id", "slug"),
        check_in("lifecycle", CLIENT_LIFECYCLES),
        check_in("own_channel_status", OWN_CHANNEL_STATUSES),
        Index(
            "uq_clients_workspace_id_own_channel_id",
            "workspace_id",
            "own_channel_id",
            unique=True,
            postgresql_where=text("own_channel_id IS NOT NULL AND deleted_at IS NULL"),
        ),
        Index("ix_clients_next_refresh_at", "next_refresh_at", postgresql_where=text("lifecycle = 'active'")),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    name: Mapped[str]
    slug: Mapped[str] = mapped_column(CITEXT())
    website_url: Mapped[str | None]
    website_domain: Mapped[str | None] = mapped_column(CITEXT())
    own_channel_id: Mapped[str | None]
    own_channel_status: Mapped[str] = mapped_column(server_default=text("'unresolved'"))
    own_channel_verification: Mapped[list[Any]] = jsonb_array()
    timezone: Mapped[str] = mapped_column(server_default=text("'UTC'"))
    country: Mapped[str | None]
    language: Mapped[str | None]
    lifecycle: Mapped[str] = mapped_column(server_default=text("'created'"))
    settings: Mapped[dict[str, Any]] = jsonb_object()
    settings_schema_version: Mapped[int] = schema_version_column()
    next_refresh_at: Mapped[datetime | None]
    last_activity_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()
    archived_at: Mapped[datetime | None]
    deleted_at: Mapped[datetime | None]


class OAuthToken(TenantColumns, Base):
    __tablename__ = "oauth_tokens"
    __zone__ = Zone.tenant
    __table_args__ = (
        client_fk(),
        check_in("provider", ("google",)),
        check_in("status", ("active", "invalid", "revoked", "purged")),
        check_in(
            "revocation_source",
            ("in_app", "google_risc", "invalid_grant", "client_deleted"),
            nullable=True,
        ),
        Index(
            "uq_oauth_tokens_client_id_provider_active",
            "client_id",
            "provider",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        Index(
            "ix_oauth_tokens_authorization_verified_at",
            "authorization_verified_at",
            postgresql_where=text("status = 'active'"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    provider: Mapped[str] = mapped_column(server_default=text("'google'"))
    google_subject: Mapped[str]
    channel_id: Mapped[str]
    scopes: Mapped[list[str]]
    key_id: Mapped[str] = mapped_column(index=True)
    encrypted_payload: Mapped[bytes]
    status: Mapped[str] = mapped_column(server_default=text("'active'"))
    approved_viewer_user_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)))
    granted_by_user_id: Mapped[uuid.UUID]
    granted_at: Mapped[datetime]
    last_refreshed_at: Mapped[datetime | None]
    authorization_verified_at: Mapped[datetime]
    failure_count: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
    last_error: Mapped[str | None]
    revoked_at: Mapped[datetime | None]
    revocation_source: Mapped[str | None]
    purge_due_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()
