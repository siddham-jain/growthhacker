from collections.abc import Awaitable, Callable, Mapping
from functools import wraps
from typing import Any

from arq.cron import CronJob, cron

from app.core.logging import get_logger
from app.core.registry import UnknownRegistrationError
from app.retention.sweeper import RetentionSweeper
from app.workers.jobs import WorkerState
from app.workers.tasks import (
    CACHE_HITS_FLUSH,
    COMPETITOR_REDISCOVERY,
    NEWS_POLL,
    OPS_ALERTS,
    OPS_ROLLUP,
    SNAPSHOT_MAINTENANCE,
    SNAPSHOT_TICK,
    maintenance_task_registry,
)

logger = get_logger(__name__)

Tick = Callable[[WorkerState], Awaitable[Mapping[str, Any]]]


def locked_tick(name: str, interval_s: int) -> Callable[[Tick], Callable[[dict[str, Any]], Awaitable[Any]]]:
    """ticks only decide and enqueue; the lock plus arq's unique slots stop overlapping runs."""

    def decorate(tick: Tick) -> Callable[[dict[str, Any]], Awaitable[Any]]:
        @wraps(tick)
        async def job(ctx: dict[str, Any]) -> Mapping[str, Any]:
            state: WorkerState = ctx["state"]
            locks = state.container.lock_manager
            lease = await locks.try_acquire(f"cron:{name}", ttl_s=max(interval_s - 5, 5))
            if lease is None:
                return {"skipped": "locked"}
            try:
                result = await tick(state)
            finally:
                await locks.release(lease)
            logger.info(
                "cron.tick", name=name, **{key: value for key, value in result.items() if key != "name"}
            )
            return result

        return job

    return decorate


@locked_tick("reconcile_runs", 120)
async def reconcile_runs(state: WorkerState) -> Mapping[str, Any]:
    return (await state.reconciler.run()).__dict__


@locked_tick("schedule_daily_refreshes", 600)
async def schedule_daily_refreshes(state: WorkerState) -> Mapping[str, Any]:
    return {"enqueued": await state.scheduler.tick_daily()}


@locked_tick("news_radar_tick", 3600)
async def news_radar_tick(state: WorkerState) -> Mapping[str, Any]:
    return {"enqueued": await state.scheduler.tick_news()}


@locked_tick("retention_sweep", 3600)
async def retention_sweep(state: WorkerState) -> Mapping[str, Any]:
    container = state.container
    settings = container.settings
    try:
        refresher = container.adapter("retention_refresher")
    except LookupError:
        refresher = None
    sweeper = RetentionSweeper(
        container.session_factory, container.clock, settings.policy, settings.retention, refresher
    )
    report = await sweeper.sweep()
    return {"rules": len(report.per_rule), "violations": len(report.violations)}


def owner_task(name: str) -> Tick:
    async def tick(state: WorkerState) -> Mapping[str, Any]:
        try:
            factory = maintenance_task_registry.get(name)
        except UnknownRegistrationError:
            return {"skipped": "not_registered"}
        return dict(await factory(state.container).run())

    tick.__name__ = name
    return tick


snapshot_tick = locked_tick(SNAPSHOT_TICK, 600)(owner_task(SNAPSHOT_TICK))
news_poll = locked_tick(NEWS_POLL, 900)(owner_task(NEWS_POLL))
ops_rollup = locked_tick(OPS_ROLLUP, 86400)(owner_task(OPS_ROLLUP))
ops_alerts = locked_tick(OPS_ALERTS, 900)(owner_task(OPS_ALERTS))
snapshot_maintenance = locked_tick(SNAPSHOT_MAINTENANCE, 86400)(owner_task(SNAPSHOT_MAINTENANCE))
cache_hits_flush = locked_tick(CACHE_HITS_FLUSH, 86400)(owner_task(CACHE_HITS_FLUSH))
competitor_rediscovery = locked_tick(COMPETITOR_REDISCOVERY, 86400)(owner_task(COMPETITOR_REDISCOVERY))


def _every(minutes: int, offset: int = 0) -> set[int]:
    return {minute for minute in range(offset, 60, minutes)}


def cron_jobs() -> list[CronJob]:
    # cadences follow each owner (99 C68); 11 only hosts them
    return [
        cron(reconcile_runs, name="reconcile_runs", minute=_every(2)),
        cron(schedule_daily_refreshes, name="schedule_daily_refreshes", minute=_every(10)),
        cron(snapshot_tick, name="snapshot_tick", minute=_every(10, 5)),
        cron(news_poll, name="news_poll", minute=_every(15, 1)),
        cron(news_radar_tick, name="news_radar_tick", minute={7}),
        cron(retention_sweep, name="retention_sweep", minute={17}),
        cron(ops_alerts, name="ops_alerts", minute=_every(15, 3)),
        cron(ops_rollup, name="ops_rollup", hour={0}, minute={40}),
        cron(cache_hits_flush, name="cache_hits_flush", hour={0}, minute={20}),
        cron(competitor_rediscovery, name="competitor_rediscovery", hour={2}, minute={30}),
        cron(snapshot_maintenance, name="snapshot_maintenance", hour={3}, minute={10}),
    ]
