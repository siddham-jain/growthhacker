from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.core.tenancy import TenantScope
from app.domain.runs import RunKind, RunNotice, RunStatus, RunTrigger, StageStatus
from app.pipeline.contracts import RunBudget, RunConfigSnapshot, UsageLedger
from app.pipeline.registry import PipelineDefinition


class RunParams(BaseModel):
    pipeline: str | None = None
    force: bool = False
    from_stage: str | None = None
    # confirm-setup analyses freeze the competitor set and rebuild topic refs in the worker first
    confirm_setup: bool = False
    setup_prepared: bool = False


class RunError(BaseModel):
    code: str
    stage: str | None = None
    message: str = Field(max_length=500)
    retryable: bool = False
    fingerprint: str | None = None


class RunTimings(BaseModel):
    queued_ms: int | None = None
    run_ms: int | None = None


@dataclass(frozen=True)
class RunPlan:
    run_id: UUID
    scope: TenantScope
    kind: RunKind
    trigger: RunTrigger
    attempt: int
    as_of: datetime
    snapshot: RunConfigSnapshot
    params: RunParams
    definition: PipelineDefinition
    budget: RunBudget
    prior_usage: UsageLedger = field(default_factory=UsageLedger)


@dataclass
class StageOutcomeView:
    status: StageStatus
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: RunError | None = None


@dataclass
class RunOutcome:
    status: RunStatus
    error: RunError | None
    notices: list[RunNotice]
    usage: UsageLedger
    stages: dict[str, StageOutcomeView]
    timings: RunTimings
