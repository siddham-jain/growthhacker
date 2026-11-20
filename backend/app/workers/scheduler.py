from collections import defaultdict, deque
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app.core.clock import Clock
from app.core.config import Settings
from app.core.db import SessionFactory
from app.core.logging import get_logger
from app.core.redis import QuotaCounter
from app.core.tenancy import TenantScope
from app.domain.runs import ClientLifecycle, RunKind, RunTrigger
from app.domain.youtube import QuotaBucket
from app.models.runs import ClientSchedule
from app.models.tenancy import Client
from app.pipeline.client_settings import ClientSettings
from app.pipeline.registry import PipelineUnavailable
from app.pipeline.runs import RunRequest, RunService, RunServiceError
from app.pipeline.schedules import ScheduleBook, jitter_minutes, next_daily_due
from app.workers.queue import ArqJobQueue

logger = get_logger(__name__)


@dataclass(frozen=True)
class DueClient:
    workspace_id: UUID
    client_id: UUID
    timezone: str
    settings: ClientSettings
    next_due_at: datetime
    cadence_hours: float
    last_enqueued_at: datetime | None


def round_robin(due: Sequence[DueClient]) -> list[DueClient]:
    """interleave workspaces, least recently served first, so one tenant cannot starve others."""
    by_workspace: dict[UUID, deque[DueClient]] = defaultdict(deque)
    served: dict[UUID, datetime] = {}
    for item in sorted(due, key=lambda item: item.next_due_at):
        by_workspace[item.workspace_id].append(item)
        if item.last_enqueued_at is not None:
            served[item.workspace_id] = max(
                served.get(item.workspace_id, item.last_enqueued_at), item.last_enqueued_at
            )
    never = datetime.min.replace(tzinfo=UTC)
    order = sorted(
        by_workspace,
        key=lambda workspace: (workspace in served, served.get(workspace, never), str(workspace)),
    )
    result: list[DueClient] = []
    while any(by_workspace[workspace] for workspace in order):
        for workspace in order:
            if by_workspace[workspace]:
                result.append(by_workspace[workspace].popleft())
    return result


class DueScheduler:
    """decides and enqueues scheduled runs from client_schedules using the db clock."""

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        runs: RunService,
        queue: ArqJobQueue,
        settings: Settings,
        quota: QuotaCounter,
        clock: Clock,
    ) -> None:
        self._session_factory = session_factory
        self._runs = runs
        self._queue = queue
        self._settings = settings
        self._quota = quota
        self._clock = clock

    async def tick_daily(self) -> int:
        return await self._tick(RunKind.daily_refresh)

    async def tick_news(self) -> int:
        return await self._tick(RunKind.news_scan)

    async def _tick(self, kind: RunKind) -> int:
        due, now = await self._due(kind)
        self._alert_overdue(kind, due, now)
        enqueued = 0
        for item in round_robin(due):
            if (
                await self._queue.depth(self._settings.workers.batch_queue)
                >= self._settings.pipeline.batch_max_depth
            ):
                break
            if kind is RunKind.daily_refresh and not await self._quota_allows():
                logger.warning("schedule.quota_forecast_short", kind=kind.value)
                break
            scope = TenantScope(workspace_id=item.workspace_id, client_id=item.client_id)
            try:
                result = await self._runs.request_run(
                    scope, RunRequest(kind=kind, trigger=RunTrigger.schedule)
                )
            except (RunServiceError, PipelineUnavailable) as error:
                logger.info("schedule.deferred", client_id=str(item.client_id), reason=str(error)[:200])
                continue
            async with self._session_factory() as session, session.begin():
                await ScheduleBook(session, scope).advance(
                    kind, next_due_at=self._next_due(kind, item, now), enqueued_at=now, run_id=result.run.id
                )
            enqueued += 1
        return enqueued

    def _next_due(self, kind: RunKind, item: DueClient, now: datetime) -> datetime:
        if kind is RunKind.news_scan:
            return now + timedelta(hours=item.cadence_hours)
        return next_daily_due(
            now,
            tz=ZoneInfo(item.timezone),
            hour=item.settings.refresh_hour_local,
            jitter=jitter_minutes(item.client_id, self._settings.pipeline.refresh_jitter_minutes),
        )

    async def _due(self, kind: RunKind) -> tuple[list[DueClient], datetime]:
        async with self._session_factory() as session:
            now = (await session.execute(select(func.now()))).scalar_one()
            statement = (
                select(ClientSchedule, Client.timezone, Client.settings)
                .join(Client, Client.id == ClientSchedule.client_id)
                .where(
                    ClientSchedule.kind == kind.value,
                    ClientSchedule.paused.is_(False),
                    ClientSchedule.next_due_at <= now,
                    Client.lifecycle == ClientLifecycle.active.value,
                    (ClientSchedule.circuit_open_until.is_(None)) | (ClientSchedule.circuit_open_until < now),
                )
                .order_by(ClientSchedule.next_due_at)
                .limit(500)
            )
            rows = (await session.execute(statement)).all()
        due = [
            DueClient(
                workspace_id=schedule.workspace_id,
                client_id=schedule.client_id,
                timezone=timezone,
                settings=ClientSettings.model_validate(settings or {}),
                next_due_at=schedule.next_due_at,
                cadence_hours=schedule.cadence_hours,
                last_enqueued_at=schedule.last_enqueued_at,
            )
            for schedule, timezone, settings in rows
        ]
        return due, now

    def _alert_overdue(self, kind: RunKind, due: list[DueClient], now: datetime) -> None:
        limit = timedelta(hours=self._settings.pipeline.overdue_alert_h)
        for item in due:
            if now - item.next_due_at > limit:
                logger.error("schedule.overdue", kind=kind.value, client_id=str(item.client_id))

    async def _quota_allows(self) -> bool:
        quota = self._settings.quota
        day = self._clock.today(ZoneInfo(quota.timezone))
        used = await self._quota.used(QuotaBucket.main.value, day)
        remaining = quota.main_daily_units - used
        return remaining - self._settings.pipeline.interactive_reserve_units >= quota.daily_refresh_run_units
