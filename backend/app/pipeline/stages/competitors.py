from collections.abc import Mapping
from typing import Any

from app.core.config import CompetitorBudgetSettings
from app.core.container import Container
from app.core.registry import UnknownRegistrationError, artifact_schema_registry, stage_registry
from app.core.tenancy import TenantScope
from app.domain.common import DomainModel
from app.domain.competitors import DriftEvent
from app.domain.profile import BusinessProfile
from app.domain.runs import RunNotice, RunNoticeCode
from app.pipeline.contracts import RunConfigSnapshot, RunContext, StageBudget, StagePolicy, StageResult
from app.pipeline.stages.website_intel import LOCATED_BUSINESS
from app.ports.artifacts import ArtifactKey
from app.services.competitors.drift.monitor import CompetitorDriftMonitor
from app.services.competitors.model import DiscoveryResult
from app.services.competitors.repository import SqlBrandChannelCache
from app.services.competitors.runner import DiscoveryRequest, DiscoveryRunner
from app.services.competitors.service import DiscoveryDeps

# produced by 03's build_profile stage
BUSINESS_PROFILE_ARTIFACT = ArtifactKey(
    name="business_profile", model=BusinessProfile, schema_version=1, contains_api_data=False
)
COMPETITOR_DISCOVERY_ARTIFACT = ArtifactKey(
    name="competitor_discovery", model=DiscoveryResult, schema_version=1, contains_api_data=True
)
NOTICE_BY_WARNING = {
    "YOUTUBE_QUOTA_LOW": RunNoticeCode.quota_limited,
    "NICHE_SEARCH_SKIPPED": RunNoticeCode.quota_limited,
    "RESOLUTION_PARTIAL": RunNoticeCode.quota_limited,
    "UPLOADS_PARTIAL": RunNoticeCode.quota_limited,
    "LLM_UNAVAILABLE": RunNoticeCode.llm_fallback,
    "WEB_SEARCH_UNAVAILABLE": RunNoticeCode.thin_data,
    "TOO_FEW_COMPETITORS": RunNoticeCode.thin_data,
}
DEGRADING_WARNINGS = frozenset(NOTICE_BY_WARNING) - {"TOO_FEW_COMPETITORS"}
_BUDGET = CompetitorBudgetSettings()


class DriftReport(DomainModel):
    events: list[DriftEvent] = []
    channels_checked: int = 0


DRIFT_ARTIFACT = ArtifactKey(
    name="competitor_drift", model=DriftReport, schema_version=1, contains_api_data=False
)
artifact_schema_registry.register(COMPETITOR_DISCOVERY_ARTIFACT.name, 1, DiscoveryResult)
artifact_schema_registry.register(DRIFT_ARTIFACT.name, 1, DriftReport)


def optional_adapter(container: Container, port: str) -> Any | None:
    # discovery degrades without web search, crawler or llm; youtube is the only hard dependency
    try:
        return container.adapter(port)
    except (LookupError, UnknownRegistrationError):
        return None


def discovery_runner(container: Container, reporter) -> DiscoveryRunner:
    deps = DiscoveryDeps(
        youtube=container.adapter("youtube"),
        web_search=optional_adapter(container, "web_search"),
        crawler=optional_adapter(container, "site_crawler"),
        llm=optional_adapter(container, "llm"),
        cache=SqlBrandChannelCache(container.session_factory),
        reporter=reporter,
    )
    return DiscoveryRunner(
        container.session_factory,
        container.lock_manager,
        deps,
        container.settings.competitors,
        container.clock,
    )


def notices(result: DiscoveryResult, stage: str) -> tuple[RunNotice, ...]:
    return tuple(
        RunNotice(code=NOTICE_BY_WARNING[warning.code], stage=stage, message=warning.message[:200])
        for warning in result.warnings
        if warning.code in NOTICE_BY_WARNING
    )


@stage_registry.register("discover_competitors")
class DiscoverCompetitorsStage:
    """onboarding: proposes the competitor set; it never auto-confirms (plan 11)."""

    name = "discover_competitors"
    version = 1
    depends_on: tuple[str, ...] = ("build_profile",)
    optional_deps: tuple[str, ...] = ()
    produces = COMPETITOR_DISCOVERY_ARTIFACT
    policy = StagePolicy(
        timeout_s=180.0,
        soft_timeout_s=_BUDGET.wall_seconds,
        critical=False,
        budget=StageBudget(
            youtube_units=_BUDGET.youtube_units,
            search_calls=_BUDGET.search_calls,
            tavily_credits=_BUDGET.tavily_credits,
            llm_usd=_BUDGET.llm_usd,
        ),
    )
    weight = 25.0

    def enabled(self, snapshot: RunConfigSnapshot) -> bool:
        return True

    def fingerprint_inputs(self, ctx: RunContext[Container]) -> Mapping[str, Any]:
        snapshot = ctx.snapshot
        return {
            "profile_version": snapshot.profile_version,
            "own_channel_id": snapshot.own_channel_id,
            "topic_set_version": snapshot.topic_set_version,
            "settings": ctx.deps.settings.competitors.model_dump(mode="json"),
        }

    async def run(self, ctx: RunContext[Container]) -> StageResult[DiscoveryResult]:
        await ctx.checkpoint()
        profile = await ctx.artifacts.get(BUSINESS_PROFILE_ARTIFACT)
        located = await ctx.artifacts.get_optional(LOCATED_BUSINESS)
        own_channel = ctx.snapshot.own_channel_id or (located.channel_id if located else None)
        request = DiscoveryRequest(
            scope=TenantScope(workspace_id=ctx.workspace_id, client_id=ctx.client_id),
            trigger="onboarding",
            profile=profile,
            pipeline_run_id=ctx.run_id,
            own_channel_ids=[own_channel] if own_channel else [],
            topic_refs=list(ctx.snapshot.topic_refs),
            ledger=ctx.budget,
        )
        result = await discovery_runner(ctx.deps, ctx.reporter).run(request)
        degraded = result.partial or any(warning.code in DEGRADING_WARNINGS for warning in result.warnings)
        return StageResult(
            status="degraded" if degraded else "succeeded",
            artifact=result,
            counts={
                "candidates": len(result.candidates),
                "competitors_proposed": len(result.proposal.competitor_channel_ids),
                "youtube_units": result.usage.yt_units,
                "search_calls": result.usage.search_calls,
            },
            notices=notices(result, self.name),
        )


@stage_registry.register("competitor_drift")
class CompetitorDriftStage:
    """daily: checks tracked competitor channels against 02's refreshed cache; zero extra youtube units."""

    name = "competitor_drift"
    version = 1
    depends_on: tuple[str, ...] = ("ingest",)
    optional_deps: tuple[str, ...] = ()
    produces = DRIFT_ARTIFACT
    policy = StagePolicy(timeout_s=60.0, critical=False)
    weight = 2.0

    def enabled(self, snapshot: RunConfigSnapshot) -> bool:
        return bool(snapshot.competitor_channel_ids)

    def fingerprint_inputs(self, ctx: RunContext[Container]) -> Mapping[str, Any]:
        return {
            "as_of_day": ctx.as_of.date().isoformat(),
            "topic_set_version": ctx.snapshot.topic_set_version,
        }

    async def run(self, ctx: RunContext[Container]) -> StageResult[DriftReport]:
        await ctx.checkpoint()
        scope = TenantScope(workspace_id=ctx.workspace_id, client_id=ctx.client_id)
        async with ctx.deps.session_factory() as session:
            monitor = CompetitorDriftMonitor(session, scope, ctx.deps.settings.competitors)
            events, checked = await monitor.run(ctx.as_of, list(ctx.snapshot.topic_refs))
            await session.commit()
        report = DriftReport(events=events, channels_checked=checked)
        missing = [event for event in events if event.kind == "channel_missing"]
        stage_notices = tuple(
            RunNotice(
                code=RunNoticeCode.competitor_unavailable,
                stage=self.name,
                message="A competitor channel is no longer available on YouTube.",
                detail={"channel_id": event.channel_id},
            )
            for event in missing
        )
        return StageResult(
            status="succeeded",
            artifact=report,
            counts={"events": len(events), "checked": checked},
            notices=stage_notices,
        )
