from collections.abc import Mapping
from typing import Any

from app.core.registry import stage_registry
from app.core.tenancy import TenantScope
from app.domain.runs import RunKind
from app.models.tenancy import Client
from app.pipeline.client_settings import read_settings
from app.pipeline.contracts import RunConfigSnapshot, RunContext, StagePolicy, StageResult
from app.pipeline.publication import PublishedResults, ResultPublisher
from app.pipeline.schedules import ScheduleBook
from app.ports.artifacts import ArtifactKey

PUBLISHED_RESULTS = ArtifactKey(
    name="published_results", model=PublishedResults, schema_version=1, contains_api_data=False
)
# lazy kinds write cards for the chosen window only, so they publish only that window
SINGLE_WINDOW_KINDS = frozenset({RunKind.cards, RunKind.news_scan})
SCHEDULED_KINDS = frozenset({RunKind.analysis, RunKind.daily_refresh})


def windows_to_publish(ctx: RunContext[Any]) -> list[int]:
    if ctx.kind in SINGLE_WINDOW_KINDS:
        return [ctx.snapshot.window_days]
    return sorted(set(ctx.snapshot.windows_computed))


@stage_registry.register("finalize")
class FinalizeStage:
    """atomic publication of the run's results, then schedule bookkeeping."""

    name = "finalize"
    version = 1
    depends_on: tuple[str, ...] = ("snapshot_config", "write_cards")
    optional_deps: tuple[str, ...] = ("analytics", "recommend", "refresh", "news", "own_analytics")
    produces = PUBLISHED_RESULTS
    policy = StagePolicy(timeout_s=60, max_attempts=2, reusable=False)
    weight = 2.0

    def enabled(self, snapshot: RunConfigSnapshot) -> bool:
        return True

    def fingerprint_inputs(self, ctx: RunContext[Any]) -> Mapping[str, Any]:
        return {"run_id": str(ctx.run_id), "windows": windows_to_publish(ctx)}

    async def run(self, ctx: RunContext[Any]) -> StageResult[PublishedResults]:
        container = ctx.deps
        scope = TenantScope(workspace_id=ctx.workspace_id, client_id=ctx.client_id)
        publisher = ResultPublisher(container.session_factory, container.adapter("run_config_source"))
        published = await publisher.publish(
            scope=scope,
            run_id=ctx.run_id,
            attempt=ctx.attempt,
            snapshot=ctx.snapshot,
            windows=windows_to_publish(ctx),
        )
        if ctx.kind in SCHEDULED_KINDS:
            await self._ensure_schedules(ctx, scope)
        return StageResult(status="succeeded", artifact=published, counts={"windows": len(published.windows)})

    async def _ensure_schedules(self, ctx: RunContext[Any], scope: TenantScope) -> None:
        container = ctx.deps
        async with container.session_factory() as session, session.begin():
            client = await session.get(Client, ctx.client_id)
            if client is None or client.workspace_id != ctx.workspace_id:
                return
            await ScheduleBook(session, scope).ensure(
                ctx.snapshot,
                now=container.clock.now(),
                refresh_hour=read_settings(client).refresh_hour_local,
                spread_minutes=container.settings.pipeline.refresh_jitter_minutes,
            )
