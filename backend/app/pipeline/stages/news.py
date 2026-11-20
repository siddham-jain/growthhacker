from collections.abc import Mapping
from typing import Any, ClassVar, Protocol
from uuid import UUID

from app.core.registry import stage_registry
from app.domain.runs import NewsFoundPayload, RunKind, RunNotice, RunNoticeCode, RunTrigger
from app.pipeline.contracts import (
    BudgetKind,
    RunConfigSnapshot,
    RunContext,
    StageBudget,
    StagePolicy,
    StageResult,
)
from app.ports.artifacts import ArtifactKey
from app.services.news.context import NewsClientContext
from app.services.news.service import NewsRadarReport, NewsRadarService

NEWS_ARTIFACT: ArtifactKey[NewsRadarReport] = ArtifactKey(
    name="news", model=NewsRadarReport, schema_version=1, contains_api_data=True
)
MAX_INSIGHTS = 10
CADENCE_SLOT_FALLBACK_MINUTES = 360


class NewsContextLoader(Protocol):
    # 03 profile + buyer questions, 04 competitors, 02 own uploads and 06 own day-7 ratios for this run
    async def load(self, client_id: UUID, snapshot: RunConfigSnapshot) -> NewsClientContext | None: ...


class NewsStageDeps(Protocol):
    news_radar: NewsRadarService
    news_context: NewsContextLoader


@stage_registry.register("news")
class NewsStage:
    name: ClassVar[str] = "news"
    version: ClassVar[int] = 1
    depends_on: ClassVar[tuple[str, ...]] = ("snapshot_config",)
    optional_deps: ClassVar[tuple[str, ...]] = ()
    produces: ClassVar[ArtifactKey[NewsRadarReport]] = NEWS_ARTIFACT
    policy: ClassVar[StagePolicy] = StagePolicy(
        timeout_s=40,
        soft_timeout_s=15,
        critical=False,
        budget=StageBudget(llm_calls=15, tavily_credits=20),
    )
    weight: ClassVar[float] = 8.0

    def enabled(self, snapshot: RunConfigSnapshot) -> bool:
        return snapshot.feature_flags.get("news_radar", True)

    def fingerprint_inputs(self, ctx: RunContext[NewsStageDeps]) -> Mapping[str, Any]:
        snapshot = ctx.snapshot
        freshness = snapshot.freshness_profile
        cadence_minutes = CADENCE_SLOT_FALLBACK_MINUTES
        if freshness is not None and freshness.refresh_policy.news_scan_interval_hours > 0:
            cadence_minutes = freshness.refresh_policy.news_scan_interval_hours * 60
        slot = int(ctx.as_of.timestamp() // (cadence_minutes * 60))
        return {
            "profile_version": snapshot.profile_version,
            "cadence_slot": slot,
            "window": snapshot.window_days,
        }

    def _notices(self, report: NewsRadarReport) -> tuple[RunNotice, ...]:
        notices = []
        if report.degraded:
            notices.append(
                RunNotice(
                    code=RunNoticeCode.news_skipped,
                    stage=self.name,
                    message="News search limited today; using official feeds only.",
                )
            )
        unavailable = report.suppressed.get("llm_unavailable", 0) + report.suppressed.get("llm_refusal", 0)
        if unavailable:
            notices.append(
                RunNotice(
                    code=RunNoticeCode.news_skipped,
                    stage=self.name,
                    message="Some news could not be judged right now; we'll retry on the next scan.",
                    detail={"stories": unavailable},
                )
            )
        return tuple(notices)

    async def _report_progress(self, ctx: RunContext[NewsStageDeps], report: NewsRadarReport) -> None:
        for draft in report.drafts[:MAX_INSIGHTS]:
            subject = draft.subject
            source = subject.reference_sources[0]
            await ctx.reporter.insight(
                f"News: {subject.key_entity}"[:200],
                NewsFoundPayload(
                    news_id=subject.news_signal_id,
                    headline=(source.title or subject.key_entity)[:200],
                    source_domain=source.publisher,
                    published_at=source.published_at,
                ),
            )

    async def run(self, ctx: RunContext[NewsStageDeps]) -> StageResult[NewsRadarReport]:
        context = await ctx.deps.news_context.load(ctx.client_id, ctx.snapshot)
        if context is None:
            notice = RunNotice(
                code=RunNoticeCode.news_skipped,
                stage=self.name,
                message="News radar skipped: no business profile.",
            )
            return StageResult(status="skipped", artifact=None, notices=(notice,))
        ctx.budget.ensure(BudgetKind.llm_calls, 1)
        # onboarding and "check news now" may fetch immediately, behind a per-client cooldown
        force = ctx.kind is RunKind.onboarding or (
            ctx.kind is RunKind.news_scan and ctx.trigger is RunTrigger.user
        )
        await ctx.reporter.step("news_scan", "Checking the news for your niche")
        report = await ctx.deps.news_radar.scan(context, now=ctx.as_of, run_id=ctx.run_id, force=force)
        if report.llm_calls:
            ctx.budget.charge(BudgetKind.llm_calls, report.llm_calls)
        if report.tavily_credits:
            ctx.budget.charge(BudgetKind.tavily_credits, report.tavily_credits)
        await self._report_progress(ctx, report)
        counts = {
            "recommendation_drafts": len(report.recommendation_drafts),
            "early_signal_drafts": len(report.early_signal_drafts),
            "review_drafts": len(report.review_drafts),
            "signals": len(report.signal_ids),
            "withdrawals": len(report.withdrawals),
        }
        status = "degraded" if report.degraded or self._notices(report) else "succeeded"
        return StageResult(status=status, artifact=report, counts=counts, notices=self._notices(report))
