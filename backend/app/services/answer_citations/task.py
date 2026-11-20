from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import select

from app.core.config import AnswerCitationSettings
from app.core.db import SessionFactory
from app.core.logging import get_logger
from app.core.tenancy import TenantScope
from app.domain.runs import ClientLifecycle
from app.domain.youtube import QuotaPriority
from app.models.tenancy import Client
from app.ports.clock import Clock
from app.services.answer_citations.source import AnswerCitationSource
from app.services.answer_citations.store import AnswerCitationStore
from app.services.competitors.classify.geo import TAVILY_COUNTRY_NAMES
from app.services.youtube.context import request_context
from app.workers.tasks import ANSWER_CITATIONS, maintenance_task_registry

logger = get_logger(__name__)


@dataclass(frozen=True)
class ClientCheck:
    questions: int
    citations: int
    credits: float


class AnswerCitationRunner:
    """checks one client's due buyer questions within the weekly question cap and a credit budget."""

    def __init__(
        self,
        source: AnswerCitationSource,
        session_factory: SessionFactory,
        clock: Clock,
        settings: AnswerCitationSettings,
    ) -> None:
        self._source = source
        self._session_factory = session_factory
        self._clock = clock
        self._settings = settings

    async def run_client(self, client: Client, *, credit_budget: float) -> ClientCheck:
        store = AnswerCitationStore(
            self._session_factory, TenantScope(workspace_id=client.workspace_id, client_id=client.id)
        )
        questions = await store.due_questions(
            self._clock.now(),
            weekly_cap=self._settings.questions_per_week,
            min_gap=timedelta(days=self._settings.min_days_between_checks),
        )
        if not questions:
            return ClientCheck(questions=0, citations=0, credits=0.0)
        ctx = request_context(QuotaPriority.background, client_id=client.id, budget_stage="answer_citations")
        checks = await self._source.check(
            questions,
            ctx,
            country=TAVILY_COUNTRY_NAMES.get(client.country or ""),
            credit_budget=min(credit_budget, len(questions)),
        )
        await store.save(checks)
        return ClientCheck(
            questions=len(checks),
            citations=sum(len(check.citations) for check in checks),
            credits=sum(check.credits for check in checks),
        )


class WeeklyAnswerCitationTask:
    """weekly: every active client's top buyer questions, under one shared credit cap for the whole sweep."""

    def __init__(self, container: Any) -> None:
        self._container = container

    async def run(self) -> Mapping[str, Any]:
        container = self._container
        settings: AnswerCitationSettings = container.settings.answer_citations
        if not settings.enabled:
            return {"skipped": "disabled"}
        try:
            search = container.adapter("answer_search")
        except LookupError as error:
            return {"skipped": "not_configured", "reason": str(error)}
        source = AnswerCitationSource(
            search, container.adapter("youtube"), container.clock, max_results=settings.max_results
        )
        runner = AnswerCitationRunner(source, container.session_factory, container.clock, settings)
        remaining = float(settings.weekly_max_credits_total)
        checked = citations = failed = 0
        clients = await self._active_clients()
        for client in clients:
            if remaining < 1:
                break
            try:
                result = await runner.run_client(client, credit_budget=remaining)
            except Exception as error:  # noqa: BLE001 - one client's failure never stops the others
                failed += 1
                logger.warning(
                    "answer_citations.client_failed", client_id=str(client.id), error=type(error).__name__
                )
                continue
            remaining -= result.credits
            checked += result.questions
            citations += result.citations
        return {
            "clients": len(clients),
            "questions": checked,
            "citations": citations,
            "credits": settings.weekly_max_credits_total - remaining,
            "failed": failed,
        }

    async def _active_clients(self) -> list[Client]:
        async with self._container.session_factory() as session:
            statement = (
                select(Client)
                .where(Client.lifecycle == ClientLifecycle.active.value, Client.deleted_at.is_(None))
                .order_by(Client.created_at, Client.id)
            )
            return list((await session.execute(statement)).scalars().all())


maintenance_task_registry.add(ANSWER_CITATIONS, WeeklyAnswerCitationTask)
