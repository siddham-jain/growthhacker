from collections.abc import Iterable, Mapping
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, Field

from app.api import ids
from app.api.schemas.common import ApiModel, WindowDays
from app.domain.runs import (
    Counts,
    EventPayload,
    RunEvent,
    RunEventType,
    RunKind,
    RunNotice,
    RunStatus,
    RunTrigger,
    StageStatus,
)
from app.models.runs import PipelineRun, StageRun
from app.pipeline.contracts import RunBudget, UsageLedger
from app.pipeline.plan import RunError, RunTimings

UserRunKind = Literal["analysis", "daily_refresh", "onboarding", "news_scan", "cards", "recrawl"]


class StageRunView(BaseModel):
    stage: str
    status: StageStatus
    attempt: int
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None
    duration_ms: int | None = None
    counts: dict[str, int] = {}
    usage: UsageLedger = UsageLedger()
    error_code: str | None = None
    reused_from_run_id: str | None = None

    @classmethod
    def from_row(cls, row: StageRun) -> "StageRunView":
        return cls(
            stage=row.stage,
            status=StageStatus(row.status),
            attempt=row.attempt,
            started_at=row.started_at,
            finished_at=row.finished_at,
            duration_ms=row.duration_ms,
            counts={key: int(value) for key, value in (row.counts or {}).items()},
            usage=UsageLedger.model_validate(row.usage) if row.usage else UsageLedger(),
            error_code=(row.error or {}).get("code"),
            reused_from_run_id=ids.encode_optional(ids.RUN, row.reused_from_run_id),
        )


class RunOut(BaseModel):
    id: str
    workspace_id: str
    client_id: str
    kind: RunKind
    pipeline: str
    trigger: RunTrigger
    status: RunStatus
    attempt: int
    resume_of_run_id: str | None = None
    follow_up_requested: bool = False
    window_days: int | None = None
    as_of: AwareDatetime
    config_hash: str
    queue: str
    priority: int
    progress_pct: float
    current_stages: list[str] = []
    stages: list[StageRunView] | None = None
    notices: list[RunNotice] = []
    usage: UsageLedger
    budget: RunBudget
    timings: RunTimings
    error: RunError | None = None
    queue_position: int | None = None
    created_at: AwareDatetime
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None
    cancel_requested_at: AwareDatetime | None = None

    @classmethod
    def from_row(
        cls,
        run: PipelineRun,
        *,
        stages: Iterable[StageRun] | None = None,
        stage_order: Iterable[str] = (),
        queue_position: int | None = None,
    ) -> "RunOut":
        params: Mapping[str, Any] = run.params or {}
        views = None
        if stages is not None:
            by_name = {row.stage: StageRunView.from_row(row) for row in stages}
            ordered = [by_name.pop(name) if name in by_name else _pending(name) for name in stage_order]
            views = [*sorted(by_name.values(), key=lambda view: view.stage), *ordered]
        return cls(
            id=ids.encode(ids.RUN, run.id),
            workspace_id=ids.encode(ids.WORKSPACE, run.workspace_id),
            client_id=ids.encode(ids.CLIENT, run.client_id),
            kind=RunKind(run.kind),
            pipeline=params.get("pipeline") or run.kind,
            trigger=RunTrigger(run.trigger),
            status=RunStatus(run.status),
            attempt=run.attempt,
            resume_of_run_id=ids.encode_optional(ids.RUN, run.resume_of_run_id),
            follow_up_requested=run.follow_up_requested,
            window_days=run.window_days,
            as_of=run.as_of,
            config_hash=run.config_hash,
            queue=run.queue,
            priority=run.priority,
            progress_pct=run.progress_pct,
            current_stages=list(run.current_stages or []),
            stages=views,
            notices=[RunNotice.model_validate(notice) for notice in run.notices or []],
            usage=UsageLedger.model_validate(run.usage) if run.usage else UsageLedger(),
            budget=RunBudget.model_validate(run.budget),
            timings=RunTimings.model_validate(run.timings or {}),
            error=RunError.model_validate(run.error) if run.error else None,
            queue_position=queue_position,
            created_at=run.created_at,
            started_at=run.started_at,
            finished_at=run.finished_at,
            cancel_requested_at=run.cancel_requested_at,
        )


def _pending(stage: str) -> StageRunView:
    return StageRunView(stage=stage, status=StageStatus.pending, attempt=0)


class RunCreate(ApiModel):
    kind: UserRunKind
    window_days: WindowDays | None = None
    force: bool = False
    from_stage: str | None = None
    resume_of: str | None = None


class RunCancelIn(ApiModel):
    reason: str | None = Field(default=None, max_length=200)


class RunRetryIn(ApiModel):
    from_stage: str | None = None


class RunEventOut(BaseModel):
    """RunEvent with api ids; clients must ignore unknown types and payload kinds."""

    run_id: str
    client_id: str
    seq: int
    type: RunEventType
    stage: str | None = None
    step: str | None = None
    status: str | None = None
    level: str
    message: str
    counts: Counts | None = None
    percent: float | None = None
    stage_percent: float | None = None
    ts: AwareDatetime
    payload: EventPayload | None = None

    @classmethod
    def from_event(cls, event: RunEvent) -> "RunEventOut":
        return cls(
            run_id=ids.encode(ids.RUN, event.run_id),
            client_id=ids.encode(ids.CLIENT, event.client_id),
            seq=event.seq,
            type=event.type,
            stage=event.stage,
            step=event.step,
            status=None if event.status is None else event.status.value,
            level=event.level,
            message=event.message,
            counts=event.counts,
            percent=event.percent,
            stage_percent=event.stage_percent,
            ts=event.ts,
            payload=event.payload,
        )


class EventsPage(BaseModel):
    items: list[RunEventOut]
    last_seq: int
    run_status: RunStatus


class StreamTokenOut(BaseModel):
    token: str
    expires_at: AwareDatetime
