"""12: the backtest dag's stand-ins for ingest (frozen_corpus) and finalize (record_backtest)."""

import asyncio
from collections.abc import Mapping
from datetime import datetime
from typing import Any, ClassVar, Protocol
from uuid import UUID

from pydantic import AwareDatetime, BaseModel

from app.core.config import Settings
from app.core.registry import stage_registry
from app.domain.runs import RunNotice, RunNoticeCode
from app.evaluation.backtest.corpus import point_in_time
from app.evaluation.backtest.replay import engine_for, frozen_view
from app.evaluation.backtest.runner import BacktestRecorder
from app.evaluation.freeze.bundle import BundleStore, FreezeBundle, LeakageError
from app.evaluation.freeze.canary import run_canary
from app.evaluation.freeze.job import analysis_request
from app.evaluation.freeze.store import FreezeRepository
from app.pipeline.contracts import RunConfigSnapshot, RunContext, StagePolicy, StageResult
from app.pipeline.stages.recommend import RECOMMEND_ARTIFACT
from app.ports.artifacts import ArtifactKey
from app.ports.clock import Clock
from app.services.analytics.inputs import AnalyticsInputs


class EvaluationDeps(Protocol):
    settings: Settings
    session_factory: Any
    clock: Clock

    def adapter(self, port_name: str) -> Any: ...


class FrozenCorpusArtifact(BaseModel):
    freeze_id: UUID
    as_of: AwareDatetime
    pit_level: str
    late_tables: list[str] = []
    channels: int
    record_counts: dict[str, int]
    canary_passed: bool
    canary_planted_rows: int
    # how the live store's view at T differs from the bundle the arms are built from
    drift: dict[str, int] = {}


class BacktestRecordArtifact(BaseModel):
    eval_id: UUID
    freeze_id: UUID
    specs_by_arm: dict[str, int]
    outcomes_mature_at: AwareDatetime
    notes: list[str] = []


FROZEN_CORPUS: ArtifactKey[FrozenCorpusArtifact] = ArtifactKey(
    name="frozen_corpus", model=FrozenCorpusArtifact, schema_version=1, contains_api_data=True
)
BACKTEST_RECORD: ArtifactKey[BacktestRecordArtifact] = ArtifactKey(
    name="backtest_record", model=BacktestRecordArtifact, schema_version=1, contains_api_data=False
)


def wall_now(ctx: RunContext[EvaluationDeps]) -> datetime:
    # ctx.clock is frozen at T in a backtest; expiry is judged on the real clock
    return ctx.deps.clock.now()


async def load_bundle(ctx: RunContext[EvaluationDeps], freeze_id: UUID) -> FreezeBundle:
    store = BundleStore.for_settings(ctx.deps.settings)
    return await asyncio.to_thread(store.read, freeze_id, wall_now(ctx))


def drift(
    bundle: AnalyticsInputs, live: AnalyticsInputs, snapshot: RunConfigSnapshot, universe: set[str]
) -> dict[str, int]:
    frozen = {v.video_id: v for v in bundle.videos}
    current = {v.video_id: v for v in live.videos}
    counts = {
        "videos_discovered_after_t": len(current.keys() - frozen.keys()),
        "videos_gone_since_t": len(frozen.keys() - current.keys()),
        "titles_edited_since_t": sum(
            frozen[vid].title != current[vid].title for vid in frozen.keys() & current.keys()
        ),
        "channels_added_after_t": len(set(snapshot.competitor_channel_ids) - universe),
    }
    return {key: value for key, value in counts.items() if value}


@stage_registry.register("frozen_corpus")
class FrozenCorpusStage:
    """loads the freeze taken at T, proves the replay is immune to post-T rows, and reports live drift."""

    name: ClassVar[str] = "frozen_corpus"
    version: ClassVar[int] = 1
    depends_on: ClassVar[tuple[str, ...]] = ("snapshot_config",)
    optional_deps: ClassVar[tuple[str, ...]] = ()
    produces: ClassVar[ArtifactKey[FrozenCorpusArtifact]] = FROZEN_CORPUS
    policy: ClassVar[StagePolicy] = StagePolicy(timeout_s=300, soft_timeout_s=180)
    weight: ClassVar[float] = 10.0

    def enabled(self, snapshot: RunConfigSnapshot) -> bool:
        return True

    def fingerprint_inputs(self, ctx: RunContext[EvaluationDeps]) -> Mapping[str, Any]:
        return {"client": str(ctx.client_id), "as_of": ctx.as_of.isoformat()}

    async def run(self, ctx: RunContext[EvaluationDeps]) -> StageResult[FrozenCorpusArtifact]:
        settings = ctx.deps.settings
        async with ctx.deps.session_factory() as session:
            row = await FreezeRepository(session).at(ctx.client_id, ctx.as_of)
        bundle = await load_bundle(ctx, row.freeze_id)
        await ctx.reporter.step(
            "frozen_corpus.canary", "Checking the replay ignores data from after the freeze"
        )
        cfg = settings.evaluation.backtest
        engine = engine_for(settings)
        canary = await asyncio.to_thread(
            run_canary,
            bundle.inputs,
            bundle.manifest.as_of,
            lambda inputs: frozen_view(
                engine, inputs, bundle, cfg.window_days, cfg.outcome_age_days
            ).fingerprint(),
            settings.evaluation.seed,
        )
        if not canary.passed:
            # a replay that moves when post-T rows appear is leakage; the run must stop (edge case 9)
            raise LeakageError("Evaluation aborted: post-T data changed the replay (leakage guard)")
        await ctx.checkpoint()
        live = await ctx.deps.adapter("analytics_inputs").load(
            analysis_request(ctx.snapshot, bundle.manifest.as_of)
        )
        changes = drift(
            bundle.inputs,
            point_in_time(live, bundle.manifest.as_of),
            ctx.snapshot,
            set(bundle.manifest.channel_ids),
        )
        notices = tuple(
            RunNotice(
                code=RunNoticeCode.data_stale,
                stage=self.name,
                message=f"Live data differs from the freeze: {key.replace('_', ' ')} ({value})"[:200],
                detail={key: value},
            )
            for key, value in sorted(changes.items())
        )
        artifact = FrozenCorpusArtifact(
            freeze_id=bundle.manifest.freeze_id,
            as_of=bundle.manifest.as_of,
            pit_level=bundle.manifest.pit_level,
            late_tables=list(bundle.manifest.late_tables),
            channels=len(bundle.manifest.channel_ids),
            record_counts=dict(bundle.manifest.record_counts),
            canary_passed=canary.passed,
            canary_planted_rows=canary.planted_rows,
            drift=changes,
        )
        return StageResult(
            status="degraded" if notices else "succeeded",
            artifact=artifact,
            counts={
                "videos": bundle.manifest.record_counts.get("videos", 0),
                "planted_rows": canary.planted_rows,
            },
            notices=notices,
        )


@stage_registry.register("record_backtest")
class RecordBacktestStage:
    """registers the evaluation and stores the system, copy-top and random specs; outcomes are scored later."""

    name: ClassVar[str] = "record_backtest"
    version: ClassVar[int] = 1
    depends_on: ClassVar[tuple[str, ...]] = ("frozen_corpus", "recommend")
    optional_deps: ClassVar[tuple[str, ...]] = ("write_cards",)
    produces: ClassVar[ArtifactKey[BacktestRecordArtifact]] = BACKTEST_RECORD
    policy: ClassVar[StagePolicy] = StagePolicy(timeout_s=300, soft_timeout_s=200, reusable=False)
    weight: ClassVar[float] = 5.0

    def enabled(self, snapshot: RunConfigSnapshot) -> bool:
        return True

    def fingerprint_inputs(self, ctx: RunContext[EvaluationDeps]) -> Mapping[str, Any]:
        cfg = ctx.deps.settings.evaluation.backtest
        return {
            "run_id": str(ctx.run_id),
            "config": cfg.model_dump(mode="json"),
            "seed": ctx.deps.settings.evaluation.seed,
        }

    async def run(self, ctx: RunContext[EvaluationDeps]) -> StageResult[BacktestRecordArtifact]:
        corpus = await ctx.artifacts.get(FROZEN_CORPUS)
        output = await ctx.artifacts.get(RECOMMEND_ARTIFACT)
        bundle = await load_bundle(ctx, corpus.freeze_id)
        recorder = BacktestRecorder(ctx.deps.session_factory, ctx.deps.settings)
        summary = await recorder.record(ctx.run_id, bundle, output, wall_now(ctx), corpus.canary_passed)
        notices: tuple[RunNotice, ...] = ()
        if not summary.specs_by_arm.get("system"):
            notices = (
                RunNotice(
                    code=RunNoticeCode.thin_data,
                    stage=self.name,
                    message="No gated cards at the freeze date; recorded as an abstention",
                ),
            )
        artifact = BacktestRecordArtifact(
            eval_id=summary.eval_id,
            freeze_id=corpus.freeze_id,
            specs_by_arm=summary.specs_by_arm,
            outcomes_mature_at=summary.outcomes_mature_at,
            notes=summary.notes,
        )
        return StageResult(
            status="degraded" if notices else "succeeded",
            artifact=artifact,
            counts={f"specs_{arm}": n for arm, n in sorted(summary.specs_by_arm.items())},
            notices=notices,
        )
