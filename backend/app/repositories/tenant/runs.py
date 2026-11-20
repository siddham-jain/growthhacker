import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from app.models.runs import ACTIVE_RUN_STATUSES, PipelineRun, RunEvent
from app.repositories.tenant.base import TenantRepository


@dataclass(frozen=True)
class RunRequestOutcome:
    run: PipelineRun
    created: bool
    reason: str

    @property
    def deduplicated(self) -> bool:
        return not self.created


class PipelineRunRepository(TenantRepository[PipelineRun]):
    """storage half of run lifecycle: idempotent creation, one active run per client, cas transitions."""

    model = PipelineRun

    async def request_run(self, values: dict[str, Any]) -> RunRequestOutcome:
        if self.scope.client_id is None:
            raise ValueError("request_run needs a client-bound scope")
        row = {**values, "workspace_id": self.scope.workspace_id, "client_id": self.scope.client_id}
        statement = insert(PipelineRun).values(**row).on_conflict_do_nothing().returning(PipelineRun)
        created = (await self.session.execute(statement)).scalar_one_or_none()
        if created is not None:
            return RunRequestOutcome(run=created, created=True, reason="created")
        key = row.get("idempotency_key")
        if key is not None:
            existing = await self._by_idempotency_key(key)
            if existing is not None:
                return RunRequestOutcome(run=existing, created=False, reason="idempotency_key")
        active = await self.active()
        if active is None:
            raise RuntimeError("run insert conflicted but no matching run exists")
        return RunRequestOutcome(run=active, created=False, reason="active_run")

    async def _by_idempotency_key(self, key: str) -> PipelineRun | None:
        statement = select(PipelineRun).where(
            PipelineRun.workspace_id == self.scope.workspace_id, PipelineRun.idempotency_key == key
        )
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def active(self) -> PipelineRun | None:
        statement = self._scoped(select(PipelineRun).where(PipelineRun.status.in_(ACTIVE_RUN_STATUSES)))
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def transition(
        self, run_id: uuid.UUID, *, from_statuses: Sequence[str], to_status: str, **changes: Any
    ) -> bool:
        statement = self._scoped_update(run_id).where(PipelineRun.status.in_(from_statuses))
        result = await self.session.execute(statement.values(status=to_status, **changes))
        return result.rowcount == 1

    async def request_cancel(self, run_id: uuid.UUID, *, at: datetime) -> bool:
        if await self.transition(run_id, from_statuses=["queued"], to_status="cancelled", finished_at=at):
            return True
        return await self.transition(
            run_id, from_statuses=["running"], to_status="cancelling", cancel_requested_at=at
        )

    async def supersede(self, run_id: uuid.UUID, *, by_run_id: uuid.UUID, at: datetime) -> bool:
        return await self.transition(
            run_id,
            from_statuses=ACTIVE_RUN_STATUSES,
            to_status="superseded",
            superseded_by_run_id=by_run_id,
            finished_at=at,
        )

    async def heartbeat(self, run_id: uuid.UUID, *, owner: str, lease_expires_at: datetime) -> bool:
        statement = (
            self._scoped_update(run_id)
            .where(PipelineRun.status.in_(ACTIVE_RUN_STATUSES))
            .where((PipelineRun.lease_owner.is_(None)) | (PipelineRun.lease_owner == owner))
            .values(lease_owner=owner, lease_expires_at=lease_expires_at)
        )
        return (await self.session.execute(statement)).rowcount == 1

    async def stale(self, *, now: datetime, limit: int = 100) -> Sequence[PipelineRun]:
        statement = self._scoped(
            select(PipelineRun).where(
                PipelineRun.status.in_(("running", "cancelling")), PipelineRun.lease_expires_at < now
            )
        ).limit(limit)
        return (await self.session.execute(statement)).scalars().all()

    async def pin_channels(self, run_id: uuid.UUID, channel_ids: Sequence[str]) -> list[str]:
        statement = self._scoped(select(PipelineRun).where(PipelineRun.id == run_id)).with_for_update()
        run = (await self.session.execute(statement)).scalar_one_or_none()
        if run is None:
            raise LookupError("run not found in this scope")
        run.pinned_channel_ids = sorted(set(run.pinned_channel_ids) | set(channel_ids))
        await self.session.flush()
        return run.pinned_channel_ids

    def _scoped_update(self, run_id: uuid.UUID):
        statement = update(PipelineRun).where(
            PipelineRun.id == run_id, PipelineRun.workspace_id == self.scope.workspace_id
        )
        if self.scope.client_id is not None:
            statement = statement.where(PipelineRun.client_id == self.scope.client_id)
        return statement


class RunEventRepository(TenantRepository[RunEvent]):
    model = RunEvent

    async def append(self, run_id: uuid.UUID, events: Sequence[dict[str, Any]]) -> list[int]:
        if not events:
            return []
        reserve = (
            update(PipelineRun)
            .where(PipelineRun.id == run_id, PipelineRun.workspace_id == self.scope.workspace_id)
            .values(event_seq=PipelineRun.event_seq + len(events))
            .returning(PipelineRun.event_seq, PipelineRun.client_id)
        )
        reserved = (await self.session.execute(reserve)).one_or_none()
        if reserved is None:
            raise LookupError("run not found in this scope")
        last_seq, client_id = reserved
        first_seq = last_seq - len(events) + 1
        rows = [
            {
                **event,
                "run_id": run_id,
                "seq": first_seq + offset,
                "workspace_id": self.scope.workspace_id,
                "client_id": client_id,
            }
            for offset, event in enumerate(events)
        ]
        await self.session.execute(insert(RunEvent).values(rows).on_conflict_do_nothing())
        return [row["seq"] for row in rows]

    async def replay(self, run_id: uuid.UUID, *, after_seq: int = 0, limit: int = 500) -> Sequence[RunEvent]:
        statement = (
            self._scoped(select(RunEvent).where(RunEvent.run_id == run_id, RunEvent.seq > after_seq))
            .order_by(RunEvent.seq)
            .limit(limit)
        )
        return (await self.session.execute(statement)).scalars().all()
