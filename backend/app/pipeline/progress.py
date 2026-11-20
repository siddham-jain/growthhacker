import asyncio
import contextlib
import json
import math
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from pydantic import TypeAdapter
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import PipelineSettings
from app.core.db import SessionFactory
from app.core.logging import get_logger
from app.core.tenancy import TenantScope
from app.domain.runs import (
    Counts,
    EventPayload,
    RunEvent,
    RunEventType,
    RunNotice,
    RunStatus,
    StageStatus,
)
from app.models.runs import PipelineRun
from app.models.runs import RunEvent as RunEventRow
from app.pipeline import progress_messages as messages

logger = get_logger(__name__)

PAYLOAD_MAX_BYTES = 4096
LIFECYCLE_EVENT_TYPES: frozenset[RunEventType] = frozenset(
    {event for event in RunEventType if event.value.startswith("run.")} | {RunEventType.results_published}
)
TERMINAL_RUN_EVENT_TYPES: frozenset[RunEventType] = frozenset(
    {
        RunEventType.run_succeeded,
        RunEventType.run_partial,
        RunEventType.run_failed,
        RunEventType.run_cancelled,
        RunEventType.run_superseded,
    }
)
STAGE_TERMINAL_EVENT_TYPES: frozenset[RunEventType] = frozenset(
    {
        RunEventType.stage_reused,
        RunEventType.stage_succeeded,
        RunEventType.stage_degraded,
        RunEventType.stage_failed,
        RunEventType.stage_skipped,
    }
)
URGENT_EVENT_TYPES = LIFECYCLE_EVENT_TYPES | STAGE_TERMINAL_EVENT_TYPES | {RunEventType.stage_started}
DROPPABLE_EVENT_TYPES: frozenset[RunEventType] = frozenset(
    {RunEventType.stage_progress, RunEventType.run_progress, RunEventType.insight, RunEventType.stage_step}
)
RUN_TERMINAL_EVENT: Mapping[RunStatus, RunEventType] = {
    RunStatus.succeeded: RunEventType.run_succeeded,
    RunStatus.partial: RunEventType.run_partial,
    RunStatus.failed: RunEventType.run_failed,
    RunStatus.cancelled: RunEventType.run_cancelled,
    RunStatus.superseded: RunEventType.run_superseded,
}
STAGE_TERMINAL_EVENT: Mapping[StageStatus, RunEventType] = {
    StageStatus.succeeded: RunEventType.stage_succeeded,
    StageStatus.reused: RunEventType.stage_reused,
    StageStatus.degraded: RunEventType.stage_degraded,
    StageStatus.failed: RunEventType.stage_failed,
    StageStatus.skipped: RunEventType.stage_skipped,
}

_payload_adapter: TypeAdapter[Any] = TypeAdapter(EventPayload)


def run_channel(prefix: str, run_id: UUID) -> str:
    return f"{prefix}:run:{run_id}"


def client_channel(prefix: str, client_id: UUID) -> str:
    return f"{prefix}:client:{client_id}"


def cancel_key(prefix: str, run_id: UUID) -> str:
    return f"{prefix}:run:{run_id}:cancel"


class EventPublisher(Protocol):
    async def publish(self, channel: str, message: str) -> None: ...


class RedisEventPublisher:
    """pub/sub is at-most-once; a lost message is backfilled by subscribers from the db."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def publish(self, channel: str, message: str) -> None:
        try:
            await self._redis.publish(channel, message)
        except (RedisError, OSError) as error:
            logger.warning("progress.publish_failed", channel=channel, error=type(error).__name__)


@dataclass(frozen=True)
class StoredEvent:
    id: int
    event: RunEvent

    def to_message(self) -> str:
        return json.dumps({"id": self.id, "event": self.event.model_dump(mode="json")})

    @classmethod
    def from_message(cls, message: str) -> "StoredEvent":
        data = json.loads(message)
        return cls(id=int(data["id"]), event=RunEvent.model_validate(data["event"]))


def _to_event(row: RunEventRow) -> StoredEvent:
    return StoredEvent(
        id=row.id,
        event=RunEvent(
            run_id=row.run_id,
            client_id=row.client_id,
            seq=row.seq,
            type=RunEventType(row.type),
            stage=row.stage,
            step=row.step,
            status=_status(row.status, row.type),
            level=row.level,
            message=row.message,
            counts=Counts.model_validate(row.counts) if row.counts else None,
            percent=row.percent,
            stage_percent=row.stage_percent,
            ts=row.created_at,
            payload=_payload_adapter.validate_python(row.payload) if row.payload else None,
        ),
    )


def _status(value: str | None, event_type: str) -> StageStatus | RunStatus | None:
    if value is None:
        return None
    return RunStatus(value) if event_type.startswith("run.") else StageStatus(value)


class EventLog:
    """gap-free per-run sequence: seq is reserved on pipeline_runs.event_seq inside the insert txn."""

    def __init__(self, session_factory: SessionFactory, scope: TenantScope) -> None:
        self._session_factory = session_factory
        self._scope = scope

    async def append(
        self,
        run_id: UUID,
        rows: Sequence[Mapping[str, Any]],
        *,
        progress: tuple[float, list[str]] | None = None,
    ) -> list[StoredEvent]:
        async with self._session_factory() as session:
            values: dict[str, Any] = {"event_seq": PipelineRun.event_seq + len(rows)}
            if progress is not None:
                values["progress_pct"] = func.greatest(PipelineRun.progress_pct, progress[0])
                values["current_stages"] = progress[1]
            reserve = (
                update(PipelineRun)
                .where(PipelineRun.id == run_id, PipelineRun.workspace_id == self._scope.workspace_id)
                .values(**values)
                .returning(PipelineRun.event_seq, PipelineRun.client_id)
            )
            reserved = (await session.execute(reserve)).one_or_none()
            if reserved is None:
                raise LookupError("run not found in this workspace")
            last_seq, client_id = reserved
            if not rows:
                await session.commit()
                return []
            first_seq = last_seq - len(rows) + 1
            statement = (
                insert(RunEventRow)
                .values(
                    [
                        {
                            **row,
                            "run_id": run_id,
                            "seq": first_seq + offset,
                            "workspace_id": self._scope.workspace_id,
                            "client_id": client_id,
                        }
                        for offset, row in enumerate(rows)
                    ]
                )
                .returning(RunEventRow)
            )
            stored = list((await session.execute(statement)).scalars().all())
            await session.commit()
        return [_to_event(row) for row in sorted(stored, key=lambda row: row.seq)]

    async def after(self, run_id: UUID, after_seq: int, *, limit: int = 500) -> list[StoredEvent]:
        statement = (
            select(RunEventRow)
            .where(
                RunEventRow.workspace_id == self._scope.workspace_id,
                RunEventRow.run_id == run_id,
                RunEventRow.seq > after_seq,
            )
            .order_by(RunEventRow.seq)
            .limit(limit)
        )
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).scalars().all()
        return [_to_event(row) for row in rows]

    async def between(self, run_id: UUID, after_seq: int, before_seq: int) -> list[StoredEvent]:
        events = await self.after(run_id, after_seq, limit=max(before_seq - after_seq - 1, 1))
        return [stored for stored in events if stored.event.seq < before_seq]

    async def client_after(self, client_id: UUID, after_id: int, *, limit: int = 500) -> list[StoredEvent]:
        statement = (
            select(RunEventRow)
            .where(
                RunEventRow.workspace_id == self._scope.workspace_id,
                RunEventRow.client_id == client_id,
                RunEventRow.id > after_id,
                RunEventRow.type.in_([event.value for event in LIFECYCLE_EVENT_TYPES]),
            )
            .order_by(RunEventRow.id)
            .limit(limit)
        )
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).scalars().all()
        return [_to_event(row) for row in rows]


@dataclass
class _StageProgress:
    weight: float
    expected_s: float
    started_at: float | None = None
    fraction: float | None = None
    terminal: bool = False


class ProgressTracker:
    """run percent = weighted stage completion; unknown totals approach 90% asymptotically."""

    def __init__(
        self, stages: Mapping[str, tuple[float, float]], monotonic: Callable[[], float] = time.monotonic
    ) -> None:
        self._stages = {
            name: _StageProgress(weight=weight, expected_s=max(expected_s, 1.0))
            for name, (weight, expected_s) in stages.items()
        }
        self._monotonic = monotonic
        self._emitted = 0.0

    def start(self, stage: str) -> None:
        self._stages[stage].started_at = self._monotonic()

    def fraction(self, stage: str, done: int, total: int | None) -> None:
        if total:
            self._stages[stage].fraction = min(max(done / total, 0.0), 1.0)

    def finish(self, stage: str) -> None:
        self._stages[stage].terminal = True

    def running(self) -> list[str]:
        return sorted(
            name
            for name, state in self._stages.items()
            if state.started_at is not None and not state.terminal
        )

    def _stage_fraction(self, state: _StageProgress) -> float:
        if state.terminal:
            return 1.0
        if state.fraction is not None:
            return state.fraction
        if state.started_at is None:
            return 0.0
        elapsed = self._monotonic() - state.started_at
        return min(1.0 - math.exp(-elapsed / state.expected_s), 0.9)

    def percent(self) -> float:
        total = sum(state.weight for state in self._stages.values())
        if total <= 0:
            return 0.0
        done = sum(state.weight * self._stage_fraction(state) for state in self._stages.values())
        computed = round(100.0 * done / total, 1)
        self._emitted = max(self._emitted, computed)
        return self._emitted


@dataclass
class _Pending:
    values: dict[str, Any]
    urgent: bool


@dataclass
class _StageCounters:
    insights: int = 0
    insights_suppressed: int = 0
    last_progress_at: float = -math.inf


class RunProgress:
    """buffer -> persist -> publish; publishing after commit keeps every seen seq replayable."""

    def __init__(
        self,
        *,
        run_id: UUID,
        client_id: UUID,
        log: EventLog,
        publisher: EventPublisher,
        prefix: str,
        settings: PipelineSettings,
        tracker: ProgressTracker,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.run_id = run_id
        self.client_id = client_id
        self.tracker = tracker
        self._log = log
        self._publisher = publisher
        self._prefix = prefix
        self._settings = settings
        self._monotonic = monotonic
        self._buffer: list[_Pending] = []
        self._lock = asyncio.Lock()
        self._timer: asyncio.Task[None] | None = None
        self._counters: dict[str, _StageCounters] = {}
        self._persisted = 0
        self._last_percent = 0.0
        self.notices: list[RunNotice] = []

    def for_stage(self, stage: str) -> "StageReporter":
        return StageReporter(self, stage)

    async def emit(
        self,
        type: RunEventType,
        message: str,
        *,
        stage: str | None = None,
        step: str | None = None,
        status: StageStatus | RunStatus | None = None,
        level: str = "info",
        counts: Counts | None = None,
        stage_percent: float | None = None,
        payload: Any = None,
    ) -> None:
        urgent = type in URGENT_EVENT_TYPES
        if not urgent and self._persisted + len(self._buffer) >= self._settings.events_per_run_max:
            return
        values = {
            "type": type.value,
            "stage": stage,
            "step": step,
            "status": None if status is None else status.value,
            "level": level,
            "message": messages.clip(message),
            "counts": None if counts is None else counts.model_dump(mode="json"),
            "percent": self.tracker.percent(),
            "stage_percent": stage_percent,
            "payload": _bounded_payload(payload),
        }
        # a percent tick goes first so a terminal event is always the run's last event
        await self._maybe_emit_percent()
        self._buffer.append(_Pending(values=values, urgent=urgent))
        self._trim_buffer()
        if urgent or len(self._buffer) >= self._settings.event_flush_batch:
            await self.flush()
        elif self._timer is None or self._timer.done():
            self._timer = asyncio.create_task(self._flush_later())

    async def stage_progress(self, stage: str, done: int, total: int | None, unit: str) -> None:
        self.tracker.fraction(stage, done, total)
        counters = self._counters.setdefault(stage, _StageCounters())
        now = self._monotonic()
        finished = total is not None and done >= total
        if not finished and now - counters.last_progress_at < self._settings.progress_min_interval_s:
            return
        counters.last_progress_at = now
        stage_percent = None if not total else round(min(done / total, 1.0) * 100.0, 1)
        await self.emit(
            RunEventType.stage_progress,
            messages.clip(
                f"{messages.stage_label(stage)}: {done}" + (f" of {total}" if total else "") + f" {unit}"
            ),
            stage=stage,
            counts=Counts(done=done, total=total, unit=unit),
            stage_percent=stage_percent,
        )

    async def insight(self, stage: str, message: str, payload: Any) -> None:
        counters = self._counters.setdefault(stage, _StageCounters())
        if counters.insights >= self._settings.insights_per_stage_max:
            counters.insights_suppressed += 1
            return
        counters.insights += 1
        await self.emit(RunEventType.insight, message, stage=stage, payload=payload)

    def suppressed_insights(self, stage: str) -> int:
        return self._counters.get(stage, _StageCounters()).insights_suppressed

    async def notice(self, notice: RunNotice) -> None:
        self.notices.append(notice)
        await self.emit(
            RunEventType.notice,
            notice.message,
            stage=notice.stage,
            level="warning",
        )

    async def flush(self) -> None:
        async with self._lock:
            if not self._buffer:
                return
            batch = list(self._buffer)
            try:
                stored = await self._log.append(
                    self.run_id,
                    [pending.values for pending in batch],
                    progress=(self.tracker.percent(), self.tracker.running()),
                )
            except (SQLAlchemyError, OSError) as error:
                logger.warning("progress.persist_failed", run_id=str(self.run_id), error=type(error).__name__)
                return
            del self._buffer[: len(batch)]
            self._persisted += len(stored)
        for event in stored:
            await self._publish(event)

    async def aclose(self) -> None:
        if self._timer is not None and not self._timer.done():
            self._timer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._timer
        for _ in range(3):
            await self.flush()
            if not self._buffer:
                return
            await asyncio.sleep(self._settings.event_flush_interval_s)
        logger.error("progress.events_lost", run_id=str(self.run_id), count=len(self._buffer))

    async def tick(self) -> None:
        # time-based progress for stages without known totals
        await self._maybe_emit_percent()
        if self._buffer:
            await self.flush()

    async def _publish(self, stored: StoredEvent) -> None:
        message = stored.to_message()
        await self._publisher.publish(run_channel(self._prefix, self.run_id), message)
        if stored.event.type in LIFECYCLE_EVENT_TYPES:
            await self._publisher.publish(client_channel(self._prefix, self.client_id), message)

    async def _flush_later(self) -> None:
        await asyncio.sleep(self._settings.event_flush_interval_s)
        await self.flush()

    async def _maybe_emit_percent(self) -> None:
        percent = self.tracker.percent()
        if percent - self._last_percent < 1.0:
            return
        self._last_percent = percent
        self._buffer.append(
            _Pending(
                values={
                    "type": RunEventType.run_progress.value,
                    "stage": None,
                    "step": None,
                    "status": None,
                    "level": "info",
                    "message": f"{percent:.0f}% complete",
                    "counts": None,
                    "percent": percent,
                    "stage_percent": None,
                    "payload": None,
                },
                urgent=False,
            )
        )

    def _trim_buffer(self) -> None:
        # db outage: drop the oldest progress-only events first, never lifecycle ones
        overflow = len(self._buffer) - self._settings.event_buffer_max
        if overflow <= 0:
            return
        kept: list[_Pending] = []
        for pending in self._buffer:
            if overflow > 0 and RunEventType(pending.values["type"]) in DROPPABLE_EVENT_TYPES:
                overflow -= 1
                continue
            kept.append(pending)
        self._buffer = kept


def _bounded_payload(payload: Any) -> dict[str, Any] | None:
    if payload is None:
        return None
    data = _payload_adapter.dump_python(payload, mode="json")
    if len(json.dumps(data).encode()) > PAYLOAD_MAX_BYTES:
        return None
    return data


@dataclass(frozen=True)
class StageReporter:
    """the ProgressReporter port bound to one stage."""

    run: RunProgress
    stage: str

    async def step(self, step: str, message: str, payload: EventPayload | None = None) -> None:
        await self.run.emit(RunEventType.stage_step, message, stage=self.stage, step=step, payload=payload)

    async def progress(self, done: int, total: int | None, unit: str) -> None:
        await self.run.stage_progress(self.stage, done, total, unit)

    async def insight(self, message: str, payload: EventPayload) -> None:
        await self.run.insight(self.stage, message, payload)

    async def notice(self, notice: RunNotice) -> None:
        await self.run.notice(notice if notice.stage else notice.model_copy(update={"stage": self.stage}))
