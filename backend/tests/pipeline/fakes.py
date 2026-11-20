import asyncio
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from pydantic import BaseModel

from app.core.registry import Registry
from app.domain.runs import RunKind
from app.pipeline.contracts import RunConfigSnapshot, RunContext, StageBudget, StagePolicy, StageResult
from app.pipeline.registry import PipelineCatalog, PipelineDefinition, validate_pipelines
from app.ports.artifacts import ArtifactKey


class FakeOut(BaseModel):
    value: int
    seen: list[str] = []


Behaviour = Callable[[RunContext[Any], "Probe"], Awaitable[StageResult[FakeOut]]]


class Probe:
    """records what fake stages did so tests can assert on calls and overlap."""

    def __init__(self) -> None:
        self.calls: Counter[str] = Counter()
        self.running = 0
        self.max_running = 0
        self.gates: dict[str, asyncio.Event] = {}

    def gate(self, name: str) -> asyncio.Event:
        return self.gates.setdefault(name, asyncio.Event())


def key(name: str) -> ArtifactKey[FakeOut]:
    return ArtifactKey(name=f"fake_{name}", model=FakeOut, schema_version=1, contains_api_data=False)


async def sum_upstream(ctx: RunContext[Any], deps: tuple[str, ...], optional: tuple[str, ...]) -> FakeOut:
    total, seen = 0, []
    for dependency in deps:
        upstream = await ctx.artifacts.get(key(dependency))
        total += upstream.value
        seen.append(dependency)
    for dependency in optional:
        upstream = await ctx.artifacts.get_optional(key(dependency))
        if upstream is not None:
            total += upstream.value
            seen.append(dependency)
    return FakeOut(value=total + 1, seen=seen)


def fake_stage(
    name: str,
    probe: Probe,
    *,
    deps: tuple[str, ...] = (),
    optional: tuple[str, ...] = (),
    behaviour: Behaviour | None = None,
    critical: bool = True,
    reusable: bool = True,
    timeout_s: float = 5,
    max_attempts: int = 2,
    version: int = 1,
    weight: float = 1.0,
    budget: Mapping[str, Any] | None = None,
) -> type:
    class FakeStage:
        def enabled(self, snapshot: RunConfigSnapshot) -> bool:
            return True

        def fingerprint_inputs(self, ctx: RunContext[Any]) -> Mapping[str, Any]:
            return {"window": ctx.window_days}

        async def run(self, ctx: RunContext[Any]) -> StageResult[FakeOut]:
            probe.calls[name] += 1
            probe.running += 1
            probe.max_running = max(probe.max_running, probe.running)
            try:
                if behaviour is not None:
                    return await behaviour(ctx, probe)
                artifact = await sum_upstream(ctx, deps, optional)
                return StageResult(status="succeeded", artifact=artifact, counts={"items": artifact.value})
            finally:
                probe.running -= 1

    FakeStage.name = name
    FakeStage.version = version
    FakeStage.depends_on = deps
    FakeStage.optional_deps = optional
    FakeStage.produces = key(name)
    FakeStage.policy = StagePolicy(
        timeout_s=timeout_s,
        max_attempts=max_attempts,
        backoff_s=(0.0,),
        critical=critical,
        reusable=reusable,
        budget=StageBudget(**(budget or {})),
    )
    FakeStage.weight = weight
    FakeStage.__name__ = f"Fake_{name}"
    return FakeStage


def catalog(stages: list[type], definitions: list[PipelineDefinition]) -> PipelineCatalog:
    stage_registry: Registry[Any] = Registry("test_stages")
    for stage in stages:
        stage_registry.add(stage.name, stage)
    pipeline_registry: Registry[PipelineDefinition] = Registry("test_pipelines")
    for definition in definitions:
        pipeline_registry.add(definition.name, definition)
    return validate_pipelines(stages=stage_registry, pipelines=pipeline_registry)


def pipeline(
    stages: tuple[str, ...],
    *,
    name: str = "analysis",
    kind: RunKind = RunKind.analysis,
    inherited: tuple[str, ...] = (),
) -> PipelineDefinition:
    return PipelineDefinition(
        name=name, kind=kind, stages=stages, inherited=inherited, drives_lifecycle=False
    )
