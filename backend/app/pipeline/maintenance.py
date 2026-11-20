from collections.abc import Mapping
from dataclasses import asdict
from datetime import timedelta
from typing import Any

from sqlalchemy import select

from app.core.logging import get_logger
from app.core.tenancy import TenantScope
from app.domain.runs import ClientLifecycle
from app.models.tenancy import Client
from app.pipeline.deps import PipelineDeps
from app.pipeline.stages.competitors import discovery_runner
from app.pipeline.stages.youtube import flush_cache_hits, snapshot_maintenance, snapshot_tick
from app.services.competitors.drift.rediscovery import RediscoveryJob, RediscoveryPolicy
from app.workers.tasks import (
    CACHE_HITS_FLUSH,
    COMPETITOR_REDISCOVERY,
    NEWS_POLL,
    SNAPSHOT_MAINTENANCE,
    SNAPSHOT_TICK,
    maintenance_task_registry,
)

logger = get_logger(__name__)


class SnapshotTickTask:
    """02: due snapshots on the batchGetStats bucket, every 10 minutes."""

    def __init__(self, container: Any) -> None:
        self._container = container

    async def run(self) -> Mapping[str, Any]:
        report = await snapshot_tick(self._container)
        return {**asdict(report), "errors": len(report.errors)}


class SnapshotMaintenanceTask:
    """02: drop untracked schedules and thin old snapshot series, daily."""

    def __init__(self, container: Any) -> None:
        self._container = container

    async def run(self) -> Mapping[str, Any]:
        return {"compacted": await snapshot_maintenance(self._container)}


class CacheHitsFlushTask:
    """02: yesterday's per-client cache-hit counts for the ops report, at 0 units."""

    def __init__(self, container: Any) -> None:
        self._container = container

    async def run(self) -> Mapping[str, Any]:
        day = (self._container.clock.now() - timedelta(days=1)).date()
        return {"day": day.isoformat(), "rows": await flush_cache_hits(self._container, day)}


class NewsPollTask:
    """08: the shared news fetch for every due query, every 15 minutes."""

    def __init__(self, container: Any) -> None:
        self._container = container

    async def run(self) -> Mapping[str, Any]:
        summary = await PipelineDeps(self._container).news_radar.poll_due(self._container.clock.now())
        return {
            "items": summary.items,
            "stories": summary.stories,
            "credits": summary.credits,
            "degraded": summary.degraded,
        }


class CompetitorRediscoveryTask:
    """04: daily check; each active client is rediscovered when its policy says it is due (monthly by default)."""

    def __init__(self, container: Any) -> None:
        self._container = container

    async def run(self) -> Mapping[str, Any]:
        container = self._container
        async with container.session_factory() as session:
            clients = (
                (
                    await session.execute(
                        select(Client).where(
                            Client.lifecycle == ClientLifecycle.active.value, Client.deleted_at.is_(None)
                        )
                    )
                )
                .scalars()
                .all()
            )
        job = RediscoveryJob(
            discovery_runner(container, None),
            container.session_factory,
            RediscoveryPolicy(container.settings.competitors),
        )
        rediscovered = failed = 0
        for client in clients:
            own = (
                [client.own_channel_id]
                if client.own_channel_status == "verified" and client.own_channel_id
                else []
            )
            try:
                result = await job.run(
                    TenantScope(workspace_id=client.workspace_id, client_id=client.id),
                    container.clock.now(),
                    own_channel_ids=own,
                )
            except Exception as error:  # noqa: BLE001 - one client's failure never stops the others
                failed += 1
                logger.warning(
                    "competitors.rediscovery_failed", client_id=str(client.id), error=type(error).__name__
                )
                continue
            rediscovered += int(result is not None)
        return {"clients": len(clients), "rediscovered": rediscovered, "failed": failed}


maintenance_task_registry.add(SNAPSHOT_TICK, SnapshotTickTask)
maintenance_task_registry.add(NEWS_POLL, NewsPollTask)
maintenance_task_registry.add(SNAPSHOT_MAINTENANCE, SnapshotMaintenanceTask)
maintenance_task_registry.add(CACHE_HITS_FLUSH, CacheHitsFlushTask)
maintenance_task_registry.add(COMPETITOR_REDISCOVERY, CompetitorRediscoveryTask)
