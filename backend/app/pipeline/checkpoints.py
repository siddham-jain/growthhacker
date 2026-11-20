from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionFactory
from app.core.tenancy import TenantScope
from app.models.runs import PipelineRun, StageRun
from app.pipeline.artifacts import PgArtifactStore
from app.ports.artifacts import ArtifactKey


class LeaseLost(RuntimeError):
    """another worker owns the run now; this one must stop writing."""


@dataclass(frozen=True)
class StageRecord:
    stage: str
    stage_version: int
    status: str
    attempt: int
    input_fingerprint: str | None = None
    reused_from_run_id: UUID | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None
    counts: Mapping[str, int] | None = None
    usage: Mapping[str, Any] | None = None
    error: Mapping[str, Any] | None = None
    warnings: list[Any] | None = None

    def values(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "stage_version": self.stage_version,
            "status": self.status,
            "attempt": self.attempt,
            "input_fingerprint": self.input_fingerprint,
            "reused_from_run_id": self.reused_from_run_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
            "counts": dict(self.counts or {}),
            "usage": dict(self.usage or {}),
            "error": None if self.error is None else dict(self.error),
            "warnings": list(self.warnings or []),
        }


class CheckpointStore:
    """stage rows and artifacts commit together, fenced by the run lease."""

    def __init__(
        self, session_factory: SessionFactory, scope: TenantScope, artifacts: PgArtifactStore, owner: str
    ) -> None:
        self._session_factory = session_factory
        self._scope = scope
        self._artifacts = artifacts
        self._owner = owner

    async def load(self, run_id: UUID) -> dict[str, StageRun]:
        statement = select(StageRun).where(
            StageRun.workspace_id == self._scope.workspace_id, StageRun.run_id == run_id
        )
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).scalars().all()
        return {row.stage: row for row in rows}

    async def record(
        self,
        run_id: UUID,
        record: StageRecord,
        *,
        artifact: tuple[ArtifactKey[Any], BaseModel, str] | None = None,
        copy_from: tuple[UUID, str] | None = None,
    ) -> None:
        async with self._session_factory() as session:
            await self._fence(session, run_id)
            if artifact is not None:
                key, value, fingerprint = artifact
                await session.execute(self._artifacts.upsert_statement(run_id, key, value, fingerprint))
            if copy_from is not None:
                source_run_id, name = copy_from
                await self._artifacts.copy_into(session, source_run_id, run_id, name)
            await session.execute(self._upsert(run_id, record))
            await session.commit()

    async def record_many(self, run_id: UUID, records: list[StageRecord]) -> None:
        if not records:
            return
        async with self._session_factory() as session:
            await self._fence(session, run_id)
            for record in records:
                await session.execute(self._upsert(run_id, record))
            await session.commit()

    async def _fence(self, session: AsyncSession, run_id: UUID) -> None:
        statement = (
            select(PipelineRun.id)
            .where(
                PipelineRun.id == run_id,
                PipelineRun.workspace_id == self._scope.workspace_id,
                PipelineRun.lease_owner == self._owner,
                PipelineRun.status.in_(("running", "cancelling")),
            )
            .with_for_update(key_share=True, read=True)
        )
        if (await session.execute(statement)).scalar_one_or_none() is None:
            raise LeaseLost(f"run {run_id} is no longer leased to {self._owner}")

    def _upsert(self, run_id: UUID, record: StageRecord):
        values = record.values()
        statement = insert(StageRun).values(
            workspace_id=self._scope.workspace_id, client_id=self._scope.client_id, run_id=run_id, **values
        )
        return statement.on_conflict_do_update(
            index_elements=[StageRun.run_id, StageRun.stage],
            set_={name: statement.excluded[name] for name in values if name != "stage"},
        )
