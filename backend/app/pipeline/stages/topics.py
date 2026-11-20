from collections.abc import Mapping
from typing import Any, ClassVar

from app.core.registry import artifact_schema_registry, stage_registry
from app.core.tenancy import TenantScope
from app.domain.runs import RunKind, RunNotice, RunNoticeCode, TopicSummaryPayload
from app.domain.topics import TopicUniverse
from app.pipeline.contracts import (
    BudgetExceeded,
    BudgetKind,
    BudgetLedger,
    RunConfigSnapshot,
    RunContext,
    StageBudget,
    StagePolicy,
    StageResult,
)
from app.ports.artifacts import ArtifactKey
from app.services.topics.engine import TopicEngine
from app.services.topics.report import TopicEngineReport

TOPIC_REPORT: ArtifactKey[TopicEngineReport] = ArtifactKey(
    name="topics", model=TopicEngineReport, schema_version=1, contains_api_data=False
)
artifact_schema_registry.register(TOPIC_REPORT.name, TOPIC_REPORT.schema_version, TopicEngineReport)

DEGRADED_NOTICES = {
    "no_llm": (RunNoticeCode.llm_fallback, "Some borderline topic matches are pending review."),
    "llm_budget": (RunNoticeCode.llm_fallback, "Some borderline topic matches will be checked later."),
    "no_embedder": (
        RunNoticeCode.thin_data,
        "Topic matching ran in reduced mode (semantic model unavailable).",
    ),
}
INSIGHT_TOPICS = 3


class LedgerSpendGuard:
    """llm spend for the topics stage, charged against the run ledger."""

    def __init__(self, ledger: BudgetLedger) -> None:
        self._ledger = ledger

    def allow(self, estimated_usd: float) -> bool:
        try:
            self._ledger.ensure(BudgetKind.llm_usd, estimated_usd)
        except BudgetExceeded:
            return False
        return True

    def record(self, cost_usd: float) -> None:
        try:
            self._ledger.charge(BudgetKind.llm_usd, cost_usd)
        except BudgetExceeded:
            return None


@stage_registry.register("topics")
class TopicsStage:
    """hybrid matching of in-scope videos to the client's own topic refs pinned in the run snapshot."""

    name: ClassVar[str] = "topics"
    version: ClassVar[int] = 1
    depends_on: ClassVar[tuple[str, ...]] = ("ingest", "enrich")
    optional_deps: ClassVar[tuple[str, ...]] = ("niche_discovery",)
    produces: ClassVar[ArtifactKey[TopicEngineReport]] = TOPIC_REPORT
    policy: ClassVar[StagePolicy] = StagePolicy(
        timeout_s=600, soft_timeout_s=480, budget=StageBudget(llm_usd=0.30)
    )
    weight: ClassVar[float] = 10.0

    def __init__(self) -> None:
        self._engines: dict[int, TopicEngine] = {}

    def enabled(self, snapshot: RunConfigSnapshot) -> bool:
        return bool(snapshot.topic_refs)

    def fingerprint_inputs(self, ctx: RunContext[Any]) -> Mapping[str, Any]:
        snapshot = ctx.snapshot
        return {
            "topic_set_version": snapshot.topic_set_version,
            "ref_fingerprints": sorted(ref.fingerprint for ref in snapshot.topic_refs),
            "engine_version": self._engine(ctx).engine_version,
        }

    def _engine(self, ctx: RunContext[Any]) -> TopicEngine:
        key = id(ctx.deps)
        if key not in self._engines:
            self._engines[key] = TopicEngine.from_container(ctx.deps)
        return self._engines[key]

    async def run(self, ctx: RunContext[Any]) -> StageResult[TopicEngineReport]:
        engine = self._engine(ctx)
        snapshot = ctx.snapshot
        scope = TenantScope(workspace_id=snapshot.workspace_id, client_id=snapshot.client_id)
        await ctx.reporter.step("topics.universe", "Loading your keywords and questions")
        loaded = await engine.load_universe(scope, snapshot.topic_set_version)
        pinned = {ref.id for ref in snapshot.topic_refs}
        if loaded is None:
            universe = engine.universe_from_refs(
                snapshot.client_id, snapshot.topic_set_version, snapshot.topic_refs
            )
            terms: list[str] = []
        else:
            stored, terms = loaded
            refs = [ref for ref in stored.refs if ref.id in pinned]
            universe = stored if len(refs) == len(stored.refs) else stored.model_copy(update={"refs": refs})
        channels = [
            channel for channel in [snapshot.own_channel_id, *snapshot.competitor_channel_ids] if channel
        ]
        video_ids = await engine.scope_video_ids(channels, ctx.as_of)
        await ctx.checkpoint()
        await ctx.reporter.step("topics.match", f"Matching {len(video_ids)} videos to your topics")
        report = await engine.match(
            scope,
            video_ids,
            universe=universe,
            non_topical_terms=terms,
            run_id=ctx.run_id,
            mode="daily" if ctx.kind is RunKind.daily_refresh else "cold",
            guard=LedgerSpendGuard(ctx.budget),
            own_channel_id=snapshot.own_channel_id,
            window_days=snapshot.window_days,
        )
        if loaded is None:
            report = report.model_copy(update={"warnings": [*report.warnings, "universe_not_persisted"]})
        await self._insights(ctx, engine, scope, universe, video_ids)
        notices = tuple(
            RunNotice(code=DEGRADED_NOTICES[mode][0], stage=self.name, message=DEGRADED_NOTICES[mode][1])
            for mode in report.degraded_modes
            if mode in DEGRADED_NOTICES
        )
        return StageResult(
            status="degraded" if report.degraded_modes else "succeeded",
            artifact=report,
            counts={
                "videos": report.videos_considered,
                "matched": report.videos_matched,
                "reused": report.videos_reused,
                "review_items": report.review_items_created,
                "llm_calls": report.llm_calls,
            },
            notices=notices,
        )

    async def _insights(
        self,
        ctx: RunContext[Any],
        engine: TopicEngine,
        scope: TenantScope,
        universe: TopicUniverse,
        video_ids: list[str],
    ) -> None:
        matches = await engine.matches(scope, video_ids)
        labels = {ref.id: ref.label for ref in universe.refs}
        counts: dict[str, list[int]] = {}
        for match in matches:
            if match.decision == "accepted":
                tally = counts.setdefault(match.topic_ref_id, [0, 0])
                tally[0] += 1
                tally[1] += int(match.review_flag)
        top = sorted(counts.items(), key=lambda item: (-item[1][0], item[0]))[:INSIGHT_TOPICS]
        for ref_id, (matched, flagged) in top:
            await ctx.reporter.insight(
                f"{matched} videos match '{labels.get(ref_id, ref_id)}'"[:200],
                TopicSummaryPayload(
                    topic_ref_id=ref_id,
                    label=labels.get(ref_id, ref_id),
                    matched_videos=matched,
                    review_flagged=flagged,
                ),
            )
