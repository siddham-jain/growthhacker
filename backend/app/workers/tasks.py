from collections.abc import Callable, Mapping
from typing import Any, Protocol

from app.core.registry import Registry


class MaintenanceTask(Protocol):
    # one cron slot of owner-specific work (02 snapshot tick, 08 news poll, 12 ops rollup/alerts)
    async def run(self) -> Mapping[str, Any]: ...


maintenance_task_registry: Registry[Callable[[Any], MaintenanceTask]] = Registry("maintenance_task_registry")

SNAPSHOT_TICK = "snapshot_tick"
NEWS_POLL = "news_poll"
OPS_ROLLUP = "ops_rollup"
OPS_ALERTS = "ops_alerts"
