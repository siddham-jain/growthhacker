from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionFactory
from app.core.registry import UnknownRegistrationError
from app.core.tenancy import TenantScope
from app.domain.runs import ClientLifecycle, RunKind, RunTrigger
from app.domain.topics import TopicKind
from app.models.tenancy import Client
from app.pipeline.client_settings import read_settings
from app.pipeline.config_source import RunConfigSource
from app.pipeline.contracts import RunConfigSnapshot
from app.pipeline.runs import ClientNotFound, RunRequest, RunRequestResult, RunService
from app.pipeline.stages.orchestration import setup_problems

CONFIRMABLE = frozenset(
    {ClientLifecycle.awaiting_confirmation, ClientLifecycle.active, ClientLifecycle.analyzing}
)


class SetupConfirmer(Protocol):
    # accepts onboarding suggestions (competitors, keywords, buyer questions) for demo auto-confirm
    async def accept_suggestions(self, scope: TenantScope) -> None: ...


class SetupNotWired(LookupError):
    pass


class SetupNotReady(ValueError):
    code = "config_invalid"


class SetupInvalid(ValueError):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class SetupValidation:
    competitors_confirmed: int
    keywords_active: int
    buyer_questions_active: int
    problems: list[str] = field(default_factory=list)

    @classmethod
    def of(cls, snapshot: RunConfigSnapshot) -> "SetupValidation":
        questions = sum(1 for ref in snapshot.topic_refs if ref.kind is TopicKind.buyer_question)
        return cls(
            competitors_confirmed=len(snapshot.competitor_channel_ids),
            keywords_active=len(snapshot.topic_refs) - questions,
            buyer_questions_active=questions,
            problems=setup_problems(snapshot),
        )


class SetupService:
    """confirm-setup: validate the confirmed inputs, then start the first analysis."""

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        config_source: RunConfigSource,
        runs: RunService,
        confirmer: SetupConfirmer | None,
    ) -> None:
        self._session_factory = session_factory
        self._config_source = config_source
        self._runs = runs
        self._confirmer = confirmer

    @classmethod
    def from_container(cls, container: Any, runs: RunService) -> "SetupService":
        try:
            confirmer = container.adapter("setup_confirmer")
        except (LookupError, UnknownRegistrationError):
            confirmer = None
        return cls(
            session_factory=container.session_factory,
            config_source=container.adapter("run_config_source"),
            runs=runs,
            confirmer=confirmer,
        )

    @staticmethod
    async def _client(session: AsyncSession, scope: TenantScope) -> Client:
        client = (
            await session.execute(
                select(Client).where(
                    Client.id == scope.client_id,
                    Client.workspace_id == scope.workspace_id,
                    Client.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if client is None:
            raise ClientNotFound("client not found")
        return client

    async def validate(self, scope: TenantScope, window_days: int | None = None) -> SetupValidation:
        async with self._session_factory() as session:
            client = await self._client(session, scope)
            if ClientLifecycle(client.lifecycle) not in CONFIRMABLE:
                raise SetupNotReady(f"client is {client.lifecycle}; onboarding has not finished")
            snapshot = await self._config_source.snapshot(session, client, window_days)
        return SetupValidation.of(snapshot)

    async def confirm(
        self,
        scope: TenantScope,
        *,
        window_days: int | None = None,
        requested_by: str | None = None,
        idempotency_key: str | None = None,
    ) -> tuple[RunRequestResult, SetupValidation]:
        validation = await self.validate(scope, window_days)
        if validation.problems:
            raise SetupInvalid(validation.problems)
        result = await self._runs.request_run(
            scope,
            RunRequest(
                kind=RunKind.analysis,
                trigger=RunTrigger.user,
                window_days=window_days,
                requested_by=requested_by,
                idempotency_key=idempotency_key,
            ),
        )
        return result, validation

    async def auto_confirm(
        self, scope: TenantScope, *, requested_by: str
    ) -> tuple[RunRequestResult, SetupValidation] | None:
        # demo mode: clients created with auto_confirm_setup skip the manual review step
        async with self._session_factory() as session:
            client = await self._client(session, scope)
        if not read_settings(client).auto_confirm_setup:
            return None
        if self._confirmer is None:
            raise SetupNotWired("no setup_confirmer adapter is registered; confirm the setup manually")
        await self._confirmer.accept_suggestions(scope)
        return await self.confirm(scope, requested_by=requested_by)
