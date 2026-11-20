from collections.abc import Mapping

from app.domain.runs import (
    ACTIVE_RUN_STATUSES,
    TERMINAL_RUN_STATUSES,
    ClientLifecycle,
    RunKind,
    RunStatus,
    StageStatus,
)


class TransitionError(ValueError):
    pass


RUN_TRANSITIONS: Mapping[RunStatus, frozenset[RunStatus]] = {
    RunStatus.queued: frozenset(
        {RunStatus.running, RunStatus.cancelled, RunStatus.superseded, RunStatus.failed}
    ),
    # running -> running is a crash recovery: same row, attempt + 1
    RunStatus.running: frozenset(
        {
            RunStatus.running,
            RunStatus.cancelling,
            RunStatus.succeeded,
            RunStatus.partial,
            RunStatus.failed,
            RunStatus.cancelled,
        }
    ),
    RunStatus.cancelling: frozenset({RunStatus.cancelled, RunStatus.failed}),
    **{status: frozenset() for status in TERMINAL_RUN_STATUSES},
}

STAGE_TERMINAL: frozenset[StageStatus] = frozenset(
    {
        StageStatus.succeeded,
        StageStatus.reused,
        StageStatus.degraded,
        StageStatus.failed,
        StageStatus.skipped,
        StageStatus.cancelled,
        StageStatus.interrupted,
    }
)
STAGE_PRODUCED: frozenset[StageStatus] = frozenset(
    {StageStatus.succeeded, StageStatus.reused, StageStatus.degraded}
)

# every attempt starts its stages at pending; reuse lookups decide what is skipped
STAGE_TRANSITIONS: Mapping[StageStatus, frozenset[StageStatus]] = {
    StageStatus.pending: frozenset(
        {StageStatus.running, StageStatus.reused, StageStatus.skipped, StageStatus.cancelled}
    ),
    StageStatus.running: frozenset(
        {
            StageStatus.succeeded,
            StageStatus.degraded,
            StageStatus.skipped,
            StageStatus.failed,
            StageStatus.cancelled,
            StageStatus.interrupted,
        }
    ),
    **{status: frozenset() for status in STAGE_TERMINAL},
}


class RunStateMachine:
    transitions = RUN_TRANSITIONS

    @classmethod
    def can(cls, current: RunStatus, target: RunStatus) -> bool:
        return target in cls.transitions[current]

    @classmethod
    def ensure(cls, current: RunStatus, target: RunStatus) -> None:
        if not cls.can(current, target):
            raise TransitionError(f"run cannot go from {current.value} to {target.value}")

    @classmethod
    def sources(cls, target: RunStatus) -> tuple[str, ...]:
        # statuses an optimistic UPDATE ... WHERE status = ANY(:sources) may move from
        return tuple(sorted(status.value for status, targets in cls.transitions.items() if target in targets))

    @staticmethod
    def is_active(status: RunStatus) -> bool:
        return status in ACTIVE_RUN_STATUSES

    @staticmethod
    def is_terminal(status: RunStatus) -> bool:
        return status in TERMINAL_RUN_STATUSES


class StageStateMachine:
    transitions = STAGE_TRANSITIONS

    @classmethod
    def can(cls, current: StageStatus, target: StageStatus) -> bool:
        return target in cls.transitions[current]

    @classmethod
    def ensure(cls, current: StageStatus, target: StageStatus) -> None:
        if not cls.can(current, target):
            raise TransitionError(f"stage cannot go from {current.value} to {target.value}")


_ONBOARDING_KINDS = frozenset({RunKind.onboarding})
_ANALYSIS_KINDS = frozenset({RunKind.analysis})


class ClientLifecycleMachine:
    """client lifecycle moves driven by runs and the api; returns None when nothing changes."""

    _manual: Mapping[ClientLifecycle, frozenset[ClientLifecycle]] = {
        ClientLifecycle.created: frozenset({ClientLifecycle.onboarding, ClientLifecycle.deleting}),
        ClientLifecycle.onboarding: frozenset(
            {
                ClientLifecycle.awaiting_confirmation,
                ClientLifecycle.onboarding_failed,
                ClientLifecycle.deleting,
            }
        ),
        ClientLifecycle.onboarding_failed: frozenset({ClientLifecycle.onboarding, ClientLifecycle.deleting}),
        ClientLifecycle.awaiting_confirmation: frozenset(
            {ClientLifecycle.analyzing, ClientLifecycle.onboarding, ClientLifecycle.deleting}
        ),
        ClientLifecycle.analyzing: frozenset(
            {ClientLifecycle.active, ClientLifecycle.awaiting_confirmation, ClientLifecycle.deleting}
        ),
        ClientLifecycle.active: frozenset(
            {ClientLifecycle.paused, ClientLifecycle.archived, ClientLifecycle.deleting}
        ),
        ClientLifecycle.paused: frozenset(
            {ClientLifecycle.active, ClientLifecycle.archived, ClientLifecycle.deleting}
        ),
        ClientLifecycle.archived: frozenset({ClientLifecycle.active, ClientLifecycle.deleting}),
        ClientLifecycle.deleting: frozenset(),
    }

    @classmethod
    def can(cls, current: ClientLifecycle, target: ClientLifecycle) -> bool:
        return target in cls._manual[current]

    @classmethod
    def ensure(cls, current: ClientLifecycle, target: ClientLifecycle) -> None:
        if not cls.can(current, target):
            raise TransitionError(f"client cannot go from {current.value} to {target.value}")

    @classmethod
    def on_run_started(cls, current: ClientLifecycle, kind: RunKind) -> ClientLifecycle | None:
        if kind in _ONBOARDING_KINDS and current in {
            ClientLifecycle.created,
            ClientLifecycle.onboarding_failed,
            ClientLifecycle.awaiting_confirmation,
        }:
            return ClientLifecycle.onboarding
        if kind in _ANALYSIS_KINDS and current is ClientLifecycle.awaiting_confirmation:
            return ClientLifecycle.analyzing
        return None

    @classmethod
    def on_run_finished(
        cls, current: ClientLifecycle, kind: RunKind, status: RunStatus
    ) -> ClientLifecycle | None:
        succeeded = status in {RunStatus.succeeded, RunStatus.partial}
        if kind in _ONBOARDING_KINDS and current is ClientLifecycle.onboarding:
            return ClientLifecycle.awaiting_confirmation if succeeded else ClientLifecycle.onboarding_failed
        if kind in _ANALYSIS_KINDS and current is ClientLifecycle.analyzing:
            return ClientLifecycle.active if succeeded else ClientLifecycle.awaiting_confirmation
        return None
