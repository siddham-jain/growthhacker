import hashlib
from datetime import datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import TenantScope
from app.domain.runs import RunKind
from app.models.runs import ClientSchedule
from app.pipeline.contracts import RunConfigSnapshot


def jitter_minutes(client_id: UUID, spread_minutes: int) -> int:
    # deterministic per client, so refreshes spread out without a thundering herd
    if spread_minutes <= 0:
        return 0
    return int(hashlib.sha256(client_id.bytes).hexdigest(), 16) % spread_minutes


def next_daily_due(now: datetime, *, tz: ZoneInfo, hour: int, jitter: int) -> datetime:
    """next local refresh_hour + jitter strictly after now; zoneinfo handles dst gaps and repeats."""
    local_now = now.astimezone(tz)
    for days in range(0, 3):
        day = (local_now + timedelta(days=days)).date()
        due = datetime.combine(day, time(hour=hour), tzinfo=tz) + timedelta(minutes=jitter)
        if due > local_now:
            return due.astimezone(now.tzinfo)
    raise AssertionError("a daily slot always exists within two days")


def news_cadence_hours(snapshot: RunConfigSnapshot) -> int:
    freshness = snapshot.freshness_profile
    return 0 if freshness is None else freshness.refresh_policy.news_scan_interval_hours


class ScheduleBook:
    """client_schedules rows: created once a client is active, advanced by the scheduler."""

    def __init__(self, session: AsyncSession, scope: TenantScope) -> None:
        if scope.client_id is None:
            raise ValueError("schedules need a client-bound scope")
        self._session = session
        self._scope = scope

    async def ensure(
        self, snapshot: RunConfigSnapshot, *, now: datetime, refresh_hour: int, spread_minutes: int
    ) -> None:
        assert self._scope.client_id is not None
        tz = ZoneInfo(snapshot.market.timezone)
        rows = [
            {
                "kind": RunKind.daily_refresh.value,
                "cadence_hours": 24.0,
                "next_due_at": next_daily_due(
                    now,
                    tz=tz,
                    hour=refresh_hour,
                    jitter=jitter_minutes(self._scope.client_id, spread_minutes),
                ),
            }
        ]
        cadence = news_cadence_hours(snapshot)
        if cadence > 0:
            rows.append(
                {
                    "kind": RunKind.news_scan.value,
                    "cadence_hours": float(cadence),
                    "next_due_at": now + timedelta(hours=cadence),
                }
            )
        for row in rows:
            statement = insert(ClientSchedule).values(
                workspace_id=self._scope.workspace_id, client_id=self._scope.client_id, **row
            )
            await self._session.execute(
                statement.on_conflict_do_update(
                    index_elements=[ClientSchedule.client_id, ClientSchedule.kind],
                    set_={"cadence_hours": statement.excluded.cadence_hours},
                )
            )

    async def advance(
        self, kind: RunKind, *, next_due_at: datetime, enqueued_at: datetime, run_id: UUID
    ) -> None:
        await self._session.execute(
            update(ClientSchedule)
            .where(ClientSchedule.client_id == self._scope.client_id, ClientSchedule.kind == kind.value)
            .values(next_due_at=next_due_at, last_enqueued_at=enqueued_at, last_run_id=run_id)
        )

    async def set_paused(self, paused: bool) -> None:
        await self._session.execute(
            update(ClientSchedule)
            .where(ClientSchedule.client_id == self._scope.client_id)
            .values(paused=paused)
        )
