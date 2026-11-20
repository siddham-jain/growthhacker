import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, SmallInteger
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    Base,
    TenantColumns,
    Zone,
    client_fk,
    created_at_column,
    jsonb_array,
)


class LLMResultCache(Base):
    __tablename__ = "llm_result_cache"
    __zone__ = Zone.global_

    key: Mapped[str] = mapped_column(primary_key=True)
    task: Mapped[str] = mapped_column(index=True)
    prompt_version: Mapped[str]
    output: Mapped[dict[str, Any]] = mapped_column(JSONB)
    model: Mapped[str]
    created_at: Mapped[datetime] = created_at_column()
    expires_at: Mapped[datetime] = mapped_column(index=True)


class RecommendationCopy(TenantColumns, Base):
    __tablename__ = "recommendation_copy"
    __zone__ = Zone.tenant
    __table_args__ = (
        client_fk(),
        Index("ix_recommendation_copy_client_id_created_at", "client_id", "created_at"),
        Index("ix_recommendation_copy_created_at", "created_at"),
    )

    draft_id: Mapped[str] = mapped_column(primary_key=True)
    recommendation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("recommendations.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pipeline_runs.id", ondelete="CASCADE"), index=True)
    copy_source: Mapped[str]
    degrade_reason: Mapped[str | None]
    prompt_id: Mapped[str]
    prompt_version: Mapped[str]
    model: Mapped[str | None]
    attempts: Mapped[int] = mapped_column(SmallInteger)
    reason_fact_ids: Mapped[list[Any]] = jsonb_array()
    fit_fact_ids: Mapped[list[Any]] = jsonb_array()
    violations_last: Mapped[list[Any]] = jsonb_array()
    critique: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    language: Mapped[str]
    created_at: Mapped[datetime] = created_at_column()


class LLMDebugPayload(Base):
    __tablename__ = "llm_debug_payloads"
    __zone__ = Zone.global_

    # no fk: per-attempt rows live in provider_usage_ledger
    call_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    request_redacted: Mapped[dict[str, Any]] = mapped_column(JSONB)
    response_text_redacted: Mapped[str | None]
    expires_at: Mapped[datetime] = mapped_column(index=True)
