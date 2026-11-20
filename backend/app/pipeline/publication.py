from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import AwareDatetime, BaseModel
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from app.core.db import SessionFactory
from app.core.tenancy import TenantScope
from app.domain.runs import ClientLifecycle, RunStatus
from app.models.runs import ActiveResultSet, PipelineRun
from app.models.tenancy import Client
from app.pipeline.config_source import RunConfigSource
from app.pipeline.contracts import RunCancelled, RunConfigSnapshot

HISTORY_DEPTH = 5


class PublicationFenced(RuntimeError):
    """the run is no longer the running attempt that may publish."""


class PublishedResults(BaseModel):
    windows: list[int]
    stale_reasons: list[str]
    published_at: AwareDatetime


class ResultPublisher:
    """one transaction swaps active_result_sets, so readers never see a half-published run."""

    def __init__(self, session_factory: SessionFactory, config_source: RunConfigSource) -> None:
        self._session_factory = session_factory
        self._config_source = config_source

    async def publish(
        self,
        *,
        scope: TenantScope,
        run_id: UUID,
        attempt: int,
        snapshot: RunConfigSnapshot,
        windows: list[int],
    ) -> PublishedResults:
        async with self._session_factory() as session, session.begin():
            run = (
                await session.execute(
                    select(PipelineRun)
                    .where(PipelineRun.id == run_id, PipelineRun.workspace_id == scope.workspace_id)
                    .with_for_update(key_share=True)
                )
            ).scalar_one_or_none()
            if run is None:
                raise PublicationFenced("run not found")
            if run.status == RunStatus.cancelling.value:
                raise RunCancelled("user")
            if run.status != RunStatus.running.value or run.attempt != attempt:
                raise PublicationFenced(f"run is {run.status} at attempt {run.attempt}")
            client = (
                await session.execute(
                    select(Client)
                    .where(Client.id == run.client_id, Client.workspace_id == scope.workspace_id)
                    .with_for_update(key_share=True)
                )
            ).scalar_one()
            if client.lifecycle == ClientLifecycle.deleting.value:
                raise RunCancelled("client_deleted")
            current = await self._config_source.snapshot(session, client, snapshot.window_days)
            stale_reasons = [] if current.config_hash == snapshot.config_hash else ["config_changed"]
            now: datetime = (await session.execute(text("SELECT now()"))).scalar_one()
            for window in windows:
                await self._swap(session, scope, run, window, now, stale_reasons)
        return PublishedResults(windows=windows, stale_reasons=stale_reasons, published_at=now)

    async def _swap(
        self,
        session,
        scope: TenantScope,
        run: PipelineRun,
        window: int,
        now: datetime,
        stale_reasons: list[str],
    ) -> None:
        previous = (
            await session.execute(
                select(ActiveResultSet)
                .where(ActiveResultSet.client_id == run.client_id, ActiveResultSet.window_days == window)
                .with_for_update(key_share=True)
            )
        ).scalar_one_or_none()
        history: list[Any] = []
        if previous is not None and previous.run_id != run.id:
            entry = {"run_id": str(previous.run_id), "published_at": previous.published_at.isoformat()}
            history = [entry, *previous.superseded_history][:HISTORY_DEPTH]
        elif previous is not None:
            history = list(previous.superseded_history)
        statement = insert(ActiveResultSet).values(
            workspace_id=scope.workspace_id,
            client_id=run.client_id,
            window_days=window,
            run_id=run.id,
            published_at=now,
            stale_reasons=stale_reasons,
            superseded_history=history,
        )
        await session.execute(
            statement.on_conflict_do_update(
                index_elements=[ActiveResultSet.client_id, ActiveResultSet.window_days],
                set_={
                    "run_id": statement.excluded.run_id,
                    "published_at": statement.excluded.published_at,
                    "stale_reasons": statement.excluded.stale_reasons,
                    "superseded_history": statement.excluded.superseded_history,
                },
            )
        )
