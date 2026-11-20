from collections.abc import Mapping
from typing import Any, Protocol
from uuid import UUID

from app.core.config import RecommendSettings
from app.core.registry import stage_registry
from app.core.tenancy import TenantScope
from app.domain.runs import RunNotice, RunNoticeCode
from app.pipeline.contracts import RunConfigSnapshot, RunContext, StagePolicy, StageResult
from app.ports.artifacts import ArtifactKey
from app.services.recommend.evidence.index import OwnVideo
from app.services.recommend.inputs import RecommendInputsProvider
from app.services.recommend.service import RecommendationService, RecommendOutput, RecommendRun
from app.services.recommend.thin_data import render_notice
from app.services.recommend.writer import CardWriter

RECOMMEND_ARTIFACT: ArtifactKey[RecommendOutput] = ArtifactKey(
    name="recommend", model=RecommendOutput, schema_version=1, contains_api_data=True
)


class RecommendationStore(Protocol):
    async def save_run(
        self, scope: TenantScope, run_id: UUID, run: RecommendRun, own: list[OwnVideo]
    ) -> int: ...


class RecommendDeps(Protocol):
    # card_writer and recommendation_store may be None: cards then use the template and nothing is persisted
    recommend_settings: RecommendSettings
    recommend_inputs: RecommendInputsProvider
    card_writer: CardWriter | None
    recommendation_store: RecommendationStore | None


@stage_registry.register("recommend")
class RecommendStage:
    """07: drafts and quality gates for all three windows, deterministic; cards for the chosen window go through
    the writer port (10) and fall back to the template renderer."""

    name = "recommend"
    version = 1
    depends_on = ("topics", "analytics")
    optional_deps = ("news",)
    produces = RECOMMEND_ARTIFACT
    policy = StagePolicy(timeout_s=90, soft_timeout_s=60)
    weight = 5.0

    def enabled(self, snapshot: RunConfigSnapshot) -> bool:
        return True

    def fingerprint_inputs(self, ctx: RunContext[RecommendDeps]) -> Mapping[str, Any]:
        snapshot = ctx.snapshot
        return {
            "window_days": snapshot.window_days,
            "competitors": sorted(snapshot.competitor_channel_ids),
            "topic_set_version": snapshot.topic_set_version,
            "config_hash": snapshot.config_hash,
        }

    async def run(self, ctx: RunContext[RecommendDeps]) -> StageResult[RecommendOutput]:
        deps = ctx.deps
        inputs = await deps.recommend_inputs.load(ctx.client_id, ctx.as_of, ctx.window_days)
        await ctx.checkpoint()
        service = RecommendationService(deps.recommend_settings)
        result = await service.generate(
            inputs, run_id=ctx.run_id, generated_at=ctx.clock.now(), writer=deps.card_writer
        )
        await ctx.checkpoint()
        measured = 0
        if deps.recommendation_store is not None:
            scope = TenantScope(workspace_id=ctx.workspace_id, client_id=ctx.client_id)
            measured = await deps.recommendation_store.save_run(scope, ctx.run_id, result, result.own_videos)
        notices = self._notices(result, deps.card_writer is not None)
        selected = result.output.window(result.output.selected_window)
        shown = len(selected.set.recommendations) if selected else 0
        counts = {
            "cards_shown": shown,
            "candidates": selected.set.candidate_count if selected else 0,
            "suppressed": sum(selected.set.suppressed_counts.values()) if selected else 0,
            "writer_fallbacks": result.writer_fallbacks,
            "outcomes_measured": measured,
        }
        degraded = result.degraded_gate is not None or bool(result.writer_fallbacks)
        return StageResult(
            status="degraded" if degraded else "succeeded",
            artifact=result.output,
            counts=counts,
            notices=tuple(notices),
        )

    def _notices(self, result: RecommendRun, writer_configured: bool) -> list[RunNotice]:
        notices: list[RunNotice] = []
        if result.writer_fallbacks and writer_configured:
            notices.append(
                RunNotice(
                    code=RunNoticeCode.llm_fallback,
                    stage=self.name,
                    message=f"{result.writer_fallbacks} cards used plainer template wording",
                    detail={"cards": result.writer_fallbacks},
                )
            )
        if result.degraded_gate is not None:
            notices.append(
                RunNotice(
                    code=RunNoticeCode.thin_data,
                    stage=self.name,
                    message="We couldn't produce reliable ideas today; every candidate failed one check",
                    detail={"gate": result.degraded_gate},
                )
            )
        selected = result.output.window(result.output.selected_window)
        for notice in selected.set.notices if selected else []:
            if notice.kind == "thin_window":
                suggest = notice.suggestion
                notices.append(
                    RunNotice(
                        code=RunNoticeCode.thin_data,
                        stage=self.name,
                        message=render_notice(notice)[:200],
                        detail=notice.found.model_dump(),
                        suggest={"widen_window_to": suggest.widen_to} if suggest.widen_to else None,
                    )
                )
            elif notice.kind == "stale_data":
                notices.append(
                    RunNotice(code=RunNoticeCode.data_stale, stage=self.name, message=render_notice(notice))
                )
        return notices
