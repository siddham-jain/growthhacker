from collections.abc import Mapping
from typing import Any, ClassVar

from app.adapters.youtube.autocomplete import YouTubeAutocomplete
from app.core.registry import artifact_schema_registry, stage_registry
from app.core.tenancy import TenantScope
from app.domain.runs import RunKind, RunNotice, RunNoticeCode
from app.domain.trends import TrendScan
from app.pipeline.contracts import BudgetKind, RunContext, StageBudget, StagePolicy, StageResult
from app.pipeline.inputs.common import latest_artifact
from app.pipeline.stages.youtube import charge_usage, data_layer, open_run_budget, youtube_context
from app.ports.artifacts import ArtifactKey
from app.services.news.context import NewsClientContext
from app.services.trends.candidates import brand_terms
from app.services.trends.service import TrendRequest, TrendScanService
from app.services.trends.writer import ClientBrief, TrendWriter
from app.services.youtube.context import SearchPool

TREND_SCAN: ArtifactKey[TrendScan] = ArtifactKey(
    name="trend_scan", model=TrendScan, schema_version=1, contains_api_data=True
)
artifact_schema_registry.register(TREND_SCAN.name, TREND_SCAN.schema_version, TrendScan)


def brief_text(context: NewsClientContext) -> str:
    profile = context.profile
    identity = profile.identity
    lines = [
        f"business: {identity.name} ({identity.niche})",
        f"summary: {identity.one_liner}",
        f"regions: {', '.join(context.regions) or 'unknown'}; languages: {', '.join(context.languages)}",
        "offerings:",
        *(f"  - {offering.name}" for offering in profile.offerings[:25]),
        "value propositions:",
        *(f"  - {value.statement}" for value in profile.value_propositions[:12]),
        "buyer questions:",
        *(f"  - {question.text}" for question in context.active_buyer_questions[:20]),
        "competitors:",
        *(f"  - {competitor.brand_name}" for competitor in context.confirmed_competitors[:10]),
    ]
    return "\n".join(lines)


@stage_registry.register("trends")
class TrendScanStage:
    """trending keywords in the client's niche and video ideas built on the videos riding them."""

    name: ClassVar[str] = "trends"
    version: ClassVar[int] = 1
    depends_on: ClassVar[tuple[str, ...]] = ("snapshot_config",)
    optional_deps: ClassVar[tuple[str, ...]] = ()
    produces: ClassVar[ArtifactKey[TrendScan]] = TREND_SCAN
    policy: ClassVar[StagePolicy] = StagePolicy(
        timeout_s=180,
        soft_timeout_s=120,
        max_attempts=2,
        critical=False,
        budget=StageBudget(youtube_units=30, search_calls=10, llm_calls=2, tavily_credits=2),
    )
    weight: ClassVar[float] = 6.0

    def enabled(self, snapshot) -> bool:
        return snapshot.feature_flags.get("trend_scan", True)

    def fingerprint_inputs(self, ctx: RunContext[Any]) -> Mapping[str, Any]:
        return {
            "profile_version": ctx.snapshot.profile_version,
            "topics": ctx.snapshot.topic_set_version,
            "competitors": sorted(ctx.snapshot.competitor_channel_ids),
            "window": ctx.snapshot.window_days,
            "pt_day": ctx.as_of.date().isoformat(),
        }

    async def run(self, ctx: RunContext[Any]) -> StageResult[TrendScan]:
        context = await ctx.deps.news_context.load(ctx.client_id, ctx.snapshot)
        if context is None:
            notice = RunNotice(
                code=RunNoticeCode.thin_data, stage=self.name, message="Trends skipped: no business profile."
            )
            return StageResult(status="skipped", artifact=None, notices=(notice,))
        settings = ctx.deps.settings.trends
        layer = data_layer(ctx)
        before = await open_run_budget(ctx, layer)
        youtube = youtube_context(ctx).model_copy(update={"search_pool": SearchPool.trends})
        llm = ctx.deps.llm
        web = ctx.deps.optional_adapter("web_search")
        if web is not None and hasattr(web, "with_attribution"):
            web = web.with_attribution(client_id=ctx.client_id, run_id=ctx.run_id)
        writer = TrendWriter(llm) if llm is not None else None
        service = TrendScanService(
            settings=settings, gateway=layer.gateway, suggester=YouTubeAutocomplete(), web=web, writer=writer
        )
        scope = TenantScope(workspace_id=ctx.workspace_id, client_id=ctx.client_id)
        previous = await latest_artifact(ctx.deps.container, scope, TREND_SCAN, exclude_run=ctx.run_id)
        budget = (
            settings.daily_refresh_keywords
            if ctx.kind is RunKind.daily_refresh
            else settings.analysis_keywords
        )
        remaining = ctx.budget.remaining(BudgetKind.search_calls)
        if remaining is not None:
            budget = min(budget, int(remaining))
        market = ctx.snapshot.market
        await ctx.reporter.step("trends.scan", "Looking for what's trending in your niche on YouTube")
        scan = await service.scan(
            TrendRequest(
                brief=ClientBrief(
                    client_id=ctx.client_id,
                    run_id=ctx.run_id,
                    text=brief_text(context),
                    language=context.languages[0] if context.languages else "en",
                    buyer_questions=[q.text for q in context.active_buyer_questions],
                    own_titles=[video.title for video in context.own_videos],
                ),
                refs=ctx.snapshot.topic_refs,
                youtube=youtube,
                brand_terms=brand_terms(
                    context.profile.identity.name, context.profile.identity.brand_aliases
                ),
                region=market.country,
                language=market.language.split("-")[0] if market.language else None,
                competitors=set(ctx.snapshot.competitor_channel_ids),
                window_days=min(ctx.snapshot.window_days, settings.max_window_days),
                keyword_budget=budget,
                previous=previous,
                now=ctx.as_of,
            )
        )
        units = await charge_usage(ctx, layer, before)
        if scan.llm_calls:
            ctx.budget.charge(BudgetKind.llm_calls, scan.llm_calls)
        if scan.tavily_credits:
            ctx.budget.charge(BudgetKind.tavily_credits, scan.tavily_credits)
        scan = scan.model_copy(update={"youtube_units": units})
        hot = sum(trend.heat == "hot" for trend in scan.keywords)
        await ctx.reporter.step(
            "trends.done",
            f"Checked {len(scan.keywords)} keywords: {hot} trending, {len(scan.ideas)} video ideas",
        )
        notices = tuple(
            RunNotice(code=RunNoticeCode.quota_limited, stage=self.name, message=message[:200])
            for message in scan.messages
            if "limit" in message
        )
        return StageResult(
            status="degraded" if notices else "succeeded",
            artifact=scan,
            counts={"keywords": len(scan.keywords), "hot": hot, "ideas": len(scan.ideas)},
            notices=notices,
        )
