from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, ClassVar, Protocol

from app.core.registry import quality_gate_registry
from app.domain.card_copy import CardCopy
from app.domain.recommendations import GateAction, GateResult, RecommendationDraft, RecType, ReferenceVideo
from app.services.recommend.context import RecommendContext
from app.services.recommend.evidence.references import ReferenceSelector
from app.services.recommend.text.copying import CopyDetector
from app.services.recommend.text.lexicon import Lexicon

ALL_TYPES: frozenset[RecType] = frozenset(RecType)


class Phase(StrEnum):
    pre_writer = "pre_writer"
    post_writer = "post_writer"


@dataclass(frozen=True, slots=True)
class GateEnv:
    # shared, per-run resources the gates read; built once per window
    ctx: RecommendContext
    copy: CopyDetector
    policy: Lexicon
    domain_policy: Lexicon | None
    certainty: Lexicon
    references: ReferenceSelector
    reannotate: Callable[[RecommendationDraft, list[ReferenceVideo]], RecommendationDraft]


@dataclass(frozen=True, slots=True)
class GateOutcome:
    gate: str
    action: GateAction
    draft: RecommendationDraft
    reason_code: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def result(self) -> GateResult:
        features = {
            key: value for key, value in self.details.items() if isinstance(value, bool | int | float | str)
        }
        return GateResult(gate=self.gate, action=self.action, reason_code=self.reason_code, features=features)


class QualityGate(Protocol):
    name: ClassVar[str]
    phase: ClassVar[Phase]
    applies_to: ClassVar[frozenset[RecType]]
    order: ClassVar[int]

    def check(self, draft: RecommendationDraft, env: GateEnv, written: CardCopy | None) -> GateOutcome: ...


def register_gate[T: type](cls: T) -> T:
    quality_gate_registry.add(cls.name, cls)
    return cls


class Gate:
    """small helpers shared by the built-in gates; 08 and 09 may subclass or implement the protocol directly."""

    name: ClassVar[str]

    def passed(self, draft: RecommendationDraft) -> GateOutcome:
        return GateOutcome(self.name, GateAction.pass_, draft)

    def repaired(self, draft: RecommendationDraft, reason_code: str, **details: Any) -> GateOutcome:
        return GateOutcome(self.name, GateAction.repair, draft, reason_code, details)

    def downgraded(self, draft: RecommendationDraft, reason_code: str, **details: Any) -> GateOutcome:
        return GateOutcome(self.name, GateAction.downgrade, draft, reason_code, details)

    def suppressed(self, draft: RecommendationDraft, reason_code: str, **details: Any) -> GateOutcome:
        return GateOutcome(self.name, GateAction.suppress, draft, reason_code, details)


@dataclass(slots=True)
class ChainResult:
    draft: RecommendationDraft
    outcomes: list[GateOutcome]

    @property
    def stopped(self) -> GateOutcome | None:
        return next((outcome for outcome in self.outcomes if outcome.action is GateAction.suppress), None)

    @property
    def violations(self) -> list[GateOutcome]:
        return [outcome for outcome in self.outcomes if outcome.action is GateAction.suppress]


class GateChain:
    """chain of responsibility over the gates registered for a phase and type, in order."""

    def __init__(self, phase: Phase, stop_on_suppress: bool = True) -> None:
        self.phase = phase
        self.stop_on_suppress = stop_on_suppress

    def gates_for(self, rec_type: RecType) -> list[QualityGate]:
        gates = [quality_gate_registry.get(key) for key in quality_gate_registry]
        chosen = [gate for gate in gates if gate.phase is self.phase and rec_type in gate.applies_to]
        return [gate() for gate in sorted(chosen, key=lambda gate: (gate.order, gate.name))]

    def run(self, draft: RecommendationDraft, env: GateEnv, written: CardCopy | None = None) -> ChainResult:
        outcomes: list[GateOutcome] = []
        current = draft
        for gate in self.gates_for(draft.type):
            outcome = gate.check(current, env, written)
            outcomes.append(outcome)
            if outcome.action is GateAction.suppress:
                if self.stop_on_suppress:
                    break
                continue
            current = outcome.draft
        results = [outcome.result() for outcome in outcomes]
        current = current.model_copy(update={"gate_results": [*current.gate_results, *results]})
        return ChainResult(draft=current, outcomes=outcomes)
