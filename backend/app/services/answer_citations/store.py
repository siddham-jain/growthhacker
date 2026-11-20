from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select

from app.core.db import SessionFactory
from app.core.tenancy import TenantScope
from app.models.answer_citations import AnswerCitation, AnswerCitationCheck
from app.models.website import BuyerQuestion
from app.services.answer_citations.models import CitationCheck, QuestionRef

WEEK = timedelta(days=7)


@dataclass(frozen=True)
class StoredCheck:
    question_id: UUID
    text: str
    funnel_stage: str
    priority: int
    checked_at: datetime
    youtube_url_count: int
    citations: list[AnswerCitation]


class AnswerCitationStore:
    """per-client checks and citations; every read is bounded to the retention window."""

    def __init__(self, session_factory: SessionFactory, scope: TenantScope) -> None:
        if scope.client_id is None:
            raise ValueError("answer citations are stored per client")
        self._session_factory = session_factory
        self._workspace_id = scope.workspace_id
        self._client_id = scope.client_id

    async def checks_since(self, since: datetime) -> int:
        async with self._session_factory() as session:
            statement = select(func.count()).where(
                AnswerCitationCheck.client_id == self._client_id, AnswerCitationCheck.checked_at >= since
            )
            return int((await session.execute(statement)).scalar_one())

    async def due_questions(self, now: datetime, *, weekly_cap: int, min_gap: timedelta) -> list[QuestionRef]:
        """the client's top confirmed questions: priority first, then the longest unchecked, within the weekly cap."""
        remaining = weekly_cap - await self.checks_since(now - WEEK)
        if remaining <= 0:
            return []
        last_checked = (
            select(
                AnswerCitationCheck.buyer_question_id,
                func.max(AnswerCitationCheck.checked_at).label("checked_at"),
            )
            .where(AnswerCitationCheck.client_id == self._client_id)
            .group_by(AnswerCitationCheck.buyer_question_id)
            .subquery()
        )
        statement = (
            select(BuyerQuestion.id, BuyerQuestion.text)
            .outerjoin(last_checked, last_checked.c.buyer_question_id == BuyerQuestion.id)
            .where(
                BuyerQuestion.workspace_id == self._workspace_id,
                BuyerQuestion.client_id == self._client_id,
                BuyerQuestion.status == "confirmed",
                (last_checked.c.checked_at.is_(None)) | (last_checked.c.checked_at < now - min_gap),
            )
            .order_by(
                BuyerQuestion.priority,
                last_checked.c.checked_at.asc().nulls_first(),
                BuyerQuestion.created_at,
                BuyerQuestion.id,
            )
            .limit(remaining)
        )
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).all()
        return [QuestionRef(id=row.id, text=row.text) for row in rows]

    async def save(self, checks: Sequence[CitationCheck]) -> None:
        async with self._session_factory() as session:
            for check in checks:
                row = AnswerCitationCheck(
                    workspace_id=self._workspace_id,
                    client_id=self._client_id,
                    buyer_question_id=check.question_id,
                    checked_at=check.checked_at,
                    result_count=check.result_count,
                    youtube_url_count=check.youtube_url_count,
                    unresolved_count=check.unresolved_count,
                    answer_returned=check.answer_returned,
                    credits=Decimal(str(round(check.credits, 2))),
                    from_cache=check.from_cache,
                )
                session.add(row)
                await session.flush()
                session.add_all(
                    AnswerCitation(
                        workspace_id=self._workspace_id,
                        client_id=self._client_id,
                        check_id=row.id,
                        buyer_question_id=check.question_id,
                        video_id=citation.video_id,
                        channel_id=citation.channel_id,
                        cited_url=citation.cited_url,
                        position=citation.position,
                        cited_in=citation.cited_in,
                        observed_at=check.checked_at,
                    )
                    for citation in check.citations
                )
            await session.commit()

    async def latest(self, since: datetime) -> list[StoredCheck]:
        """the newest check per buyer question since the cutoff, with its citations."""
        ranked = (
            select(
                AnswerCitationCheck.id,
                func.row_number()
                .over(
                    partition_by=AnswerCitationCheck.buyer_question_id,
                    order_by=AnswerCitationCheck.checked_at.desc(),
                )
                .label("rank"),
            )
            .where(AnswerCitationCheck.client_id == self._client_id, AnswerCitationCheck.checked_at >= since)
            .subquery()
        )
        statement = (
            select(AnswerCitationCheck, BuyerQuestion)
            .join(ranked, ranked.c.id == AnswerCitationCheck.id)
            .join(BuyerQuestion, BuyerQuestion.id == AnswerCitationCheck.buyer_question_id)
            .where(ranked.c.rank == 1, BuyerQuestion.status == "confirmed")
            .order_by(BuyerQuestion.priority, BuyerQuestion.created_at, BuyerQuestion.id)
        )
        async with self._session_factory() as session:
            pairs = (await session.execute(statement)).all()
            check_ids = [check.id for check, _ in pairs]
            citations = (
                (
                    await session.execute(
                        select(AnswerCitation)
                        .where(AnswerCitation.check_id.in_(check_ids))
                        .order_by(AnswerCitation.position.asc().nulls_last(), AnswerCitation.video_id)
                    )
                )
                .scalars()
                .all()
                if check_ids
                else []
            )
        by_check: dict[UUID, list[AnswerCitation]] = {}
        for citation in citations:
            by_check.setdefault(citation.check_id, []).append(citation)
        return [
            StoredCheck(
                question_id=question.id,
                text=question.text,
                funnel_stage=question.funnel_stage,
                priority=question.priority,
                checked_at=check.checked_at,
                youtube_url_count=check.youtube_url_count,
                citations=by_check.get(check.id, []),
            )
            for check, question in pairs
        ]
