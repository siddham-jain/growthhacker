import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import ForeignKey, Index, SmallInteger, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantColumns, Zone, check_in, client_fk, uuid_pk
from app.retention.rules import DataClass, RetentionAction, RetentionRule, retention_registry

CITED_IN = ("result", "answer")


class AnswerCitationCheck(TenantColumns, Base):
    """one web answer search for one buyer question; kept even when no youtube video was cited."""

    __tablename__ = "answer_citation_checks"
    __zone__ = Zone.tenant
    __table_args__ = (
        client_fk(),
        Index("ix_answer_citation_checks_client_id_checked_at", "client_id", "checked_at"),
        Index("ix_answer_citation_checks_buyer_question_id_checked_at", "buyer_question_id", "checked_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    buyer_question_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("buyer_questions.id", ondelete="CASCADE"))
    checked_at: Mapped[datetime]
    result_count: Mapped[int] = mapped_column(SmallInteger)
    youtube_url_count: Mapped[int] = mapped_column(SmallInteger)
    unresolved_count: Mapped[int] = mapped_column(SmallInteger)
    answer_returned: Mapped[bool]
    credits: Mapped[Decimal]
    from_cache: Mapped[bool]


class AnswerCitation(TenantColumns, Base):
    """a youtube video a web answer cited, resolved through videos.list; channel_id is api data."""

    __tablename__ = "answer_citations"
    __zone__ = Zone.tenant
    __table_args__ = (
        client_fk(),
        UniqueConstraint("check_id", "video_id"),
        check_in("cited_in", CITED_IN),
        Index("ix_answer_citations_client_id_observed_at", "client_id", "observed_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    check_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("answer_citation_checks.id", ondelete="CASCADE"), index=True
    )
    buyer_question_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("buyer_questions.id", ondelete="CASCADE"))
    video_id: Mapped[str]
    channel_id: Mapped[str]
    cited_url: Mapped[str]
    position: Mapped[int | None] = mapped_column(SmallInteger)
    cited_in: Mapped[str]
    observed_at: Mapped[datetime]


# both tables hold api-derived rows, so they follow the 28-day act-by clock (policy.retention_days)
for _rule in (
    RetentionRule(
        name="answer_citation_checks",
        table="answer_citation_checks",
        data_class=DataClass.tenant_derived_from_api,
        action=RetentionAction.delete,
        clock="checked_at",
    ),
    RetentionRule(
        name="answer_citations",
        table="answer_citations",
        data_class=DataClass.tenant_derived_from_api,
        action=RetentionAction.delete,
        clock="observed_at",
    ),
):
    retention_registry.add(_rule.name, _rule)
