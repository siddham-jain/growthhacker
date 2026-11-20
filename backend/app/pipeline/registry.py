from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal

from app.core.registry import Registry, stage_registry
from app.domain.runs import RunKind, RunTrigger
from app.ports.artifacts import ArtifactKey

QueueRole = Literal["interactive", "batch"]


class PipelineConfigError(RuntimeError):
    pass


class PipelineUnavailable(LookupError):
    def __init__(self, pipeline: str, reasons: Iterable[str]) -> None:
        self.pipeline = pipeline
        self.reasons = tuple(reasons)
        super().__init__(f"pipeline '{pipeline}' is not runnable: {'; '.join(self.reasons)}")


@dataclass(frozen=True)
class PipelineDefinition:
    """stages run in this pipeline; inherited stages are read from the client's latest artifacts."""

    name: str
    kind: RunKind
    stages: tuple[str, ...]
    inherited: tuple[str, ...] = ()
    # dependency name -> stage of this pipeline that stands in for it (backtest: ingest -> frozen_corpus)
    substitutes: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    queue: QueueRole = "interactive"
    scheduled_queue: QueueRole = "batch"
    priority: int = 0
    drives_lifecycle: bool = True

    def resolve(self, dependency: str) -> str:
        return self.substitutes.get(dependency, dependency)

    def queue_for(self, trigger: RunTrigger) -> QueueRole:
        if trigger in {RunTrigger.schedule, RunTrigger.evaluation}:
            return self.scheduled_queue
        return self.queue

    @property
    def members(self) -> frozenset[str]:
        return frozenset(self.stages) | frozenset(self.inherited)


pipeline_registry: Registry[PipelineDefinition] = Registry("pipeline_registry")


def register_pipeline(definition: PipelineDefinition) -> PipelineDefinition:
    return pipeline_registry.add(definition.name, definition)


def _stage_checks(name: str, stage: Any) -> list[str]:
    problems = []
    if getattr(stage, "name", None) != name:
        problems.append(f"stage registered as '{name}' reports name '{getattr(stage, 'name', None)}'")
    if not isinstance(getattr(stage, "produces", None), ArtifactKey):
        problems.append(f"stage '{name}' must declare produces as an ArtifactKey")
    weight = getattr(stage, "weight", 0)
    if not isinstance(weight, int | float) or weight <= 0:
        problems.append(f"stage '{name}' weight must be > 0")
    policy = getattr(stage, "policy", None)
    if policy is None or not policy.idempotent:
        problems.append(f"stage '{name}' must be idempotent")
    return problems


def _graph_checks(definition: PipelineDefinition, stages: Mapping[str, Any]) -> list[str]:
    problems = []
    members = definition.members
    if overlap := set(definition.stages) & set(definition.inherited):
        problems.append(f"{definition.name}: stages are both run and inherited: {sorted(overlap)}")
    for alias, target in definition.substitutes.items():
        if target not in definition.stages:
            problems.append(f"{definition.name}: substitute for '{alias}' is not a stage: '{target}'")
    edges: dict[str, set[str]] = {name: set() for name in definition.stages}
    for name in definition.stages:
        stage = stages[name]
        for dependency in stage.depends_on:
            resolved = definition.resolve(dependency)
            if resolved not in members:
                problems.append(f"{definition.name}: '{name}' depends on '{dependency}' outside the pipeline")
            elif resolved in edges:
                edges[name].add(resolved)
        for dependency in stage.optional_deps:
            resolved = definition.resolve(dependency)
            if resolved in edges:
                edges[name].add(resolved)
    if name := _first_cycle_member(edges):
        problems.append(f"{definition.name}: dependency cycle through '{name}'")
    return problems


def _first_cycle_member(edges: Mapping[str, set[str]]) -> str | None:
    # kahn's algorithm; anything left with unmet dependencies sits on a cycle
    remaining = {name: len(deps) for name, deps in edges.items()}
    dependents: dict[str, list[str]] = {name: [] for name in edges}
    for name, deps in edges.items():
        for dependency in deps:
            dependents[dependency].append(name)
    queue = deque(name for name, count in remaining.items() if count == 0)
    while queue:
        current = queue.popleft()
        for dependent in dependents[current]:
            remaining[dependent] -= 1
            if remaining[dependent] == 0:
                queue.append(dependent)
    blocked = sorted(name for name, count in remaining.items() if count > 0)
    return blocked[0] if blocked else None


@dataclass(frozen=True)
class PipelineCatalog:
    available: Mapping[str, PipelineDefinition]
    # pipeline name -> reasons it cannot run (unregistered stages, unresolved dependencies)
    unavailable: Mapping[str, tuple[str, ...]]
    stages: Mapping[str, Any]

    def require(self, name: str) -> PipelineDefinition:
        if name in self.available:
            return self.available[name]
        raise PipelineUnavailable(name, self.unavailable.get(name, (f"unknown pipeline '{name}'",)))

    def stage(self, name: str) -> Any:
        return self.stages[name]


def validate_pipelines(
    stages: Registry[Any] = stage_registry,
    pipelines: Registry[PipelineDefinition] = pipeline_registry,
    *,
    require_complete: bool = False,
) -> PipelineCatalog:
    """stage faults always raise; incomplete pipelines raise only when require_complete, else are unavailable."""
    instances = {name: factory() for name, factory in stages.all().items()}
    stage_problems = [problem for name, stage in instances.items() for problem in _stage_checks(name, stage)]
    if stage_problems:
        raise PipelineConfigError("invalid stage registration:\n- " + "\n- ".join(stage_problems))
    available: dict[str, PipelineDefinition] = {}
    unavailable: dict[str, tuple[str, ...]] = {}
    for name, definition in pipelines.all().items():
        absent = sorted(stage for stage in definition.members if stage not in instances)
        reasons = [f"unregistered stage '{stage}'" for stage in absent] or _graph_checks(
            definition, instances
        )
        if reasons:
            unavailable[name] = tuple(reasons)
        else:
            available[name] = definition
    if require_complete and unavailable:
        details = [f"{name}: {reason}" for name, reasons in unavailable.items() for reason in reasons]
        raise PipelineConfigError("incomplete pipeline configuration:\n- " + "\n- ".join(details))
    return PipelineCatalog(
        available=MappingProxyType(available), unavailable=MappingProxyType(unavailable), stages=instances
    )
