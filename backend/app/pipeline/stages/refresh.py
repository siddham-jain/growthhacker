from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any, ClassVar, Protocol

from app.core.config import RefreshSettings
from app.core.registry import artifact_schema_registry, stage_registry
from app.domain.runs import RefreshSuggestedPayload, RunNotice, RunNoticeCode
from app.pipeline.contracts import (
    BudgetKind,
    RunConfigSnapshot,
    RunContext,
    StageBudget,
    StagePolicy,
    StageResult,
)
from app.ports.artifacts import ArtifactKey
from app.ports.clock import Clock
from app.ports.llm import LLMClient
from app.services.refresh.engine import RefreshEngine
from app.services.refresh.inputs import RefreshInputs
from app.services.refresh.links.checker import LinkProbe
from app.services.refresh.links.health import LinkHealthService, LinkStatusCache
from app.services.refresh.output import RefreshOutput
from app.services.refresh.store import RefreshStore

REFRESH_ARTIFACT: ArtifactKey[RefreshOutput] = ArtifactKey(
    name="refresh", model=RefreshOutput, schema_version=1, contains_api_data=True
)
artifact_schema_registry.register(REFRESH_ARTIFACT.name, REFRESH_ARTIFACT.schema_version, RefreshOutput)
HISTORY_DAYS = 60


class RefreshInputSource(Protocol):
    """assembles upstream artifacts (02, 03, 05, 06, 08) into one frozen RefreshInputs for this run."""

    async def load(self, ctx: RunContext[Any]) -> RefreshInputs: ...


class RefreshDeps(Protocol):
    refresh_settings: RefreshSettings
    refresh_inputs: RefreshInputSource
    refresh_store: RefreshStore | None
    link_probe: LinkProbe | None
    link_cache: LinkStatusCache | None
    llm: LLMClient | None
    clock: Clock


@stage_registry.register("refresh")
class RefreshStage:
    name: ClassVar[str] = "refresh"
    version: ClassVar[int] = 1
    depends_on: ClassVar[tuple[str, ...]] = ("topics", "analytics")
    optional_deps: ClassVar[tuple[str, ...]] = ("news",)
    produces: ClassVar[ArtifactKey[RefreshOutput]] = REFRESH_ARTIFACT
    policy: ClassVar[StagePolicy] = StagePolicy(
        timeout_s=60,
        soft_timeout_s=30,
        critical=False,
        budget=StageBudget(search_calls=0, llm_calls=20),
    )
    weight: ClassVar[float] = 4.0

    def enabled(self, snapshot: RunConfigSnapshot) -> bool:
        return snapshot.own_channel_id is not None and snapshot.feature_flags.get("description_refresh", True)

    def fingerprint_inputs(self, ctx: RunContext[RefreshDeps]) -> Mapping[str, Any]:
        settings = ctx.deps.refresh_settings
        return {"as_of_day": ctx.as_of.date().isoformat(), "settings": settings.model_dump(mode="json")}

    async def run(self, ctx: RunContext[RefreshDeps]) -> StageResult[RefreshOutput]:
        deps = ctx.deps
        inputs = await deps.refresh_inputs.load(ctx)
        if not inputs.channel_verified:
            await ctx.reporter.step("refresh.skipped", "Confirm your channel to get refresh suggestions.")
            return StageResult(status="skipped", artifact=None)
        store = deps.refresh_store
        if store is not None:
            history, impacts, last_eval = await store.load_history(ctx.as_of - timedelta(days=HISTORY_DAYS))
            inputs = inputs.model_copy(
                update={
                    "previous_refreshes": history,
                    "past_impacts": impacts,
                    "last_keyword_eval_at": last_eval,
                }
            )
        await ctx.checkpoint()
        health = LinkHealthService(deps.refresh_settings, deps.clock, deps.link_probe, deps.link_cache)
        llm = deps.llm if self._llm_budget_left(ctx) else None
        output = await RefreshEngine(deps.refresh_settings, health, llm).run(inputs)
        await ctx.checkpoint()
        if store is not None:
            await store.save_run(
                output.refreshes,
                output.eligibility,
                output.status_changes,
                output.impacts,
                self._keyword_eval_at(output, inputs.last_keyword_eval_at, ctx.as_of),
            )
        await self._report(ctx, output, inputs)
        notices = self._notices(output)
        status = "degraded" if notices else "succeeded"
        return StageResult(status=status, artifact=output, counts=dict(output.counts), notices=notices)

    @staticmethod
    def _llm_budget_left(ctx: RunContext[RefreshDeps]) -> bool:
        remaining = ctx.budget.remaining(BudgetKind.llm_calls)
        return remaining is None or remaining > 0

    @staticmethod
    def _keyword_eval_at(output: RefreshOutput, previous: datetime | None, now: datetime) -> datetime | None:
        return now if output.keyword_checks_ran else previous

    @staticmethod
    async def _report(ctx: RunContext[RefreshDeps], output: RefreshOutput, inputs: RefreshInputs) -> None:
        eligibility = output.eligibility
        message = (
            f"refresh: decision={eligibility.decision}, {eligibility.evergreen_video_count} evergreen videos"
        )
        await ctx.reporter.step("refresh.decision", message[:200])
        titles = {video.video_id: video.title for video in inputs.own_videos}
        for refresh in output.refreshes:
            if not refresh.operations:
                continue
            await ctx.reporter.insight(
                output.headline[:200],
                RefreshSuggestedPayload(
                    video_id=refresh.video_id,
                    title=titles.get(refresh.video_id, ""),
                    insertions=len(refresh.operations),
                ),
            )

    @staticmethod
    def _notices(output: RefreshOutput) -> tuple[RunNotice, ...]:
        notices = []
        if output.counts.get("llm_fallbacks"):
            notices.append(
                RunNotice(
                    code=RunNoticeCode.llm_fallback,
                    stage="refresh",
                    message="Some refresh lines use our standard wording because rephrasing was unavailable.",
                )
            )
        if output.counts.get("links_deferred"):
            notices.append(
                RunNotice(
                    code=RunNoticeCode.data_stale,
                    stage="refresh",
                    message="Some links weren't checked this run; we'll check them on the next refresh.",
                    detail={"links_deferred": output.counts["links_deferred"]},
                )
            )
        return tuple(notices)
