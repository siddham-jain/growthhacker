"""maintenance tasks the worker cron runs by name (11 hosts the cadence, 12 owns the work)."""

from collections.abc import Mapping
from typing import Any

from app.evaluation.ops.alerts import AlertEvaluator, default_notifiers
from app.evaluation.ops.hooks import run_nightly_hooks
from app.evaluation.ops.rollup import OpsRollupTask
from app.workers.tasks import OPS_ALERTS, OPS_ROLLUP, maintenance_task_registry


class NightlyOpsTask:
    """rolls up yesterday, then runs the nightly housekeeping hooks."""

    def __init__(self, container: Any) -> None:
        self.container = container
        self.rollup = OpsRollupTask(container.session_factory, container.settings, container.clock)

    async def run(self) -> Mapping[str, Any]:
        result = await self.rollup.roll(self.rollup.yesterday())
        return {**result, "hooks": await run_nightly_hooks(self.container)}


class AlertsTask:
    def __init__(self, container: Any) -> None:
        self.evaluator = AlertEvaluator(
            container.session_factory,
            container.settings,
            container.clock,
            default_notifiers(container.settings, container.http),
        )

    async def run(self) -> Mapping[str, Any]:
        return (await self.evaluator.evaluate()).as_dict()


maintenance_task_registry.add(OPS_ROLLUP, NightlyOpsTask)
maintenance_task_registry.add(OPS_ALERTS, AlertsTask)
