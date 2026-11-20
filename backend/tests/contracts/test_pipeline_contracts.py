from collections.abc import Mapping
from dataclasses import replace
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from pydantic import BaseModel

from app.domain.runs import EventPayload, RunKind, RunNotice, RunTrigger
from app.pipeline.contracts import (
    BudgetKind,
    RunCancelled,
    RunConfigSnapshot,
    RunContext,
    Stage,
    StagePolicy,
    StageResult,
    UsageLedger,
)
from app.ports.artifacts import ArtifactKey
from tests.contracts.factories import CLIENT_ID, GENERATED_AT, RUN_ID


class CountsArtifact(BaseModel):
    videos: int


class FrozenClock:
    def now(self) -> datetime:
        return GENERATED_AT

    def today(self, tz: ZoneInfo) -> date:
        return GENERATED_AT.astimezone(tz).date()


class RecordingReporter:
    def __init__(self) -> None:
        self.steps: list[str] = []

    async def step(self, step: str, message: str, payload: EventPayload | None = None) -> None:
        self.steps.append(step)

    async def progress(self, done: int, total: int | None, unit: str) -> None: ...

    async def insight(self, message: str, payload: EventPayload) -> None: ...

    async def notice(self, notice: RunNotice) -> None: ...


class UnlimitedLedger:
    def ensure(self, kind: BudgetKind, amount: float) -> None: ...

    def charge(self, kind: BudgetKind, amount: float) -> None: ...

    def remaining(self, kind: BudgetKind) -> float | None:
        return None

    def usage(self) -> UsageLedger:
        return UsageLedger()


class EmptyArtifacts:
    async def get(self, key: ArtifactKey[Any]) -> Any:
        raise KeyError(key.name)

    async def get_optional(self, key: ArtifactKey[Any]) -> Any:
        return None


class Token:
    def __init__(self, cancelled: bool = False) -> None:
        self.cancelled = cancelled

    def is_cancelled(self) -> bool:
        return self.cancelled

    async def checkpoint(self) -> None:
        if self.cancelled:
            raise RunCancelled


COUNTS_KEY = ArtifactKey(name="counts", model=CountsArtifact, schema_version=1, contains_api_data=False)


class CountingStage:
    name = "count_videos"
    version = 1
    depends_on: tuple[str, ...] = ()
    optional_deps: tuple[str, ...] = ()
    produces = COUNTS_KEY
    policy = StagePolicy(timeout_s=30.0)
    weight = 1.0

    def enabled(self, snapshot: RunConfigSnapshot) -> bool:
        return True

    def fingerprint_inputs(self, ctx: RunContext[None]) -> Mapping[str, Any]:
        return {"window_days": ctx.window_days}

    async def run(self, ctx: RunContext[None]) -> StageResult[CountsArtifact]:
        await ctx.checkpoint()
        await ctx.reporter.step("count.videos", "Counting videos")
        return StageResult(status="succeeded", artifact=CountsArtifact(videos=12), counts={"videos": 12})


def make_context(cancelled: bool = False) -> RunContext[None]:
    snapshot = RunConfigSnapshot(
        workspace_id=CLIENT_ID,
        client_id=CLIENT_ID,
        config_version=1,
        config_hash="abc",
        window_days=30,
        topic_set_version=1,
        market={"country": "IN", "language": "en", "timezone": "Asia/Kolkata"},
    )
    return RunContext(
        run_id=RUN_ID,
        kind=RunKind.analysis,
        trigger=RunTrigger.user,
        attempt=1,
        as_of=GENERATED_AT,
        snapshot=snapshot,
        clock=FrozenClock(),
        budget=UnlimitedLedger(),
        reporter=RecordingReporter(),
        artifacts=EmptyArtifacts(),
        cancel=Token(cancelled),
        deps=None,
    )


async def test_a_stage_runs_against_the_context_contract():
    stage: Stage[CountsArtifact, None] = CountingStage()
    reporter = RecordingReporter()
    ctx = make_context().for_stage(stage.name, reporter)

    result = await stage.run(ctx)

    assert result.artifact == CountsArtifact(videos=12)
    assert reporter.steps == ["count.videos"]
    assert ctx.stage == "count_videos"
    assert ctx.client_id == CLIENT_ID
    assert ctx.windows == (7, 30, 90)
    assert stage.fingerprint_inputs(ctx) == {"window_days": 30}


async def test_checkpoint_raises_once_cancelled():
    with pytest.raises(RunCancelled):
        await CountingStage().run(make_context(cancelled=True))


def test_stage_policy_rejects_unsafe_settings():
    with pytest.raises(ValueError, match="idempotent"):
        StagePolicy(timeout_s=10.0, idempotent=False)
    with pytest.raises(ValueError, match="soft_timeout_s"):
        StagePolicy(timeout_s=10.0, soft_timeout_s=10.0)


def test_succeeded_stage_must_produce_an_artifact():
    with pytest.raises(ValueError, match="artifact"):
        StageResult(status="succeeded", artifact=None)
    assert StageResult(status="skipped", artifact=None).artifact is None


def test_run_context_requires_an_aware_as_of():
    with pytest.raises(ValueError, match="timezone-aware"):
        replace(make_context(), as_of=datetime(2026, 11, 19))
