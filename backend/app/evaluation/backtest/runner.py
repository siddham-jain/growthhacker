"""record the arms at T (called by the backtest dag) and score them once post-T outcomes have matured."""

import asyncio
import os
import subprocess
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.core.config import REPO_ROOT, Settings
from app.domain.recommendations import RecommendationDraft
from app.evaluation.backtest.arms import ArmContext, generate_arms
from app.evaluation.backtest.frame import ClientEvaluation, build_frame, matched_arms
from app.evaluation.backtest.models import ArmSpec, BacktestConfig, BacktestReport
from app.evaluation.backtest.outcomes import FrozenBaselines, collect_outcomes
from app.evaluation.backtest.replay import engine_for, frozen_view, outlier_threshold
from app.evaluation.backtest.scoring import score, scoring_settings
from app.evaluation.backtest.specs import system_specs
from app.evaluation.backtest.store import BacktestStore
from app.evaluation.freeze.bundle import FreezeBundle
from app.evaluation.freeze.job import analysis_request
from app.evaluation.freeze.store import FreezeRepository
from app.models.recommendations import Recommendation as RecommendationRow
from app.services.recommend.service import RecommendOutput

EVAL_NAMESPACE = uuid.UUID("6f1d8d5e-9b8a-4c1e-8a57-1b0f6c2a9e12")


class OutcomesNotMature(RuntimeError):
    pass


def eval_id_for(run_id: uuid.UUID) -> uuid.UUID:
    # one evaluation per backtest run, stable across retries so the stage stays idempotent
    return uuid.uuid5(EVAL_NAMESPACE, f"backtest:{run_id}")


def code_version() -> str:
    if sha := os.environ.get("GIT_SHA"):
        return sha
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=2
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return out.stdout.strip() or "unknown"


def build_config(
    settings: Settings,
    bundle: FreezeBundle,
    run_id: uuid.UUID,
    version: str,
    baselines: FrozenBaselines,
    canary_passed: bool,
) -> BacktestConfig:
    cfg = settings.evaluation.backtest
    manifest = bundle.manifest
    return BacktestConfig(
        client_id=manifest.client_id,
        workspace_id=manifest.workspace_id,
        freeze_id=manifest.freeze_id,
        run_id=run_id,
        as_of=manifest.as_of,
        window_days=cfg.window_days,
        horizon_days=cfg.horizon_days,
        outcome_age_days=cfg.outcome_age_days,
        k_per_type=dict(cfg.k_per_type),
        random_replicates=cfg.random_replicates,
        bootstrap_resamples=cfg.bootstrap_resamples,
        alpha=cfg.alpha,
        seed=settings.evaluation.seed,
        # the threshold frozen with the bundle; a tuned one only applies to later freezes (edge case 12)
        outlier_threshold=outlier_threshold(settings),
        include_own_channel=cfg.include_own_channel,
        floors={
            "min_universe_videos_client": cfg.min_universe_videos_client,
            "min_matched_videos_client": cfg.min_matched_videos_client,
            "min_universe_videos_pooled": cfg.min_universe_videos_pooled,
            "min_matched_videos_pooled": cfg.min_matched_videos_pooled,
            "min_channels_pooled": cfg.min_channels_pooled,
        },
        max_missing_share=cfg.max_missing_share,
        max_usd=cfg.max_usd,
        max_quota_units=cfg.max_quota_units,
        code_version=version,
        snapshot=bundle.snapshot,
        universe=list(manifest.channel_ids),
        own_channel_id=manifest.own_channel_id,
        frozen_baselines=baselines.to_json(),
        leakage_canary_passed=canary_passed,
        pit_level=manifest.pit_level,
        late_tables=list(manifest.late_tables),
    )


@dataclass
class RecordSummary:
    eval_id: uuid.UUID
    specs_by_arm: dict[str, int]
    outcomes_mature_at: datetime
    notes: list[str] = field(default_factory=list)


class BacktestRecorder:
    def __init__(self, session_factory: Any, settings: Settings) -> None:
        self.session_factory = session_factory
        self.settings = settings

    async def record(
        self,
        run_id: uuid.UUID,
        bundle: FreezeBundle,
        output: RecommendOutput,
        now: datetime,
        canary_passed: bool,
    ) -> RecordSummary:
        cfg = self.settings.evaluation.backtest
        manifest = bundle.manifest
        view = await asyncio.to_thread(
            frozen_view,
            engine_for(self.settings),
            bundle.inputs,
            bundle,
            cfg.window_days,
            cfg.outcome_age_days,
        )
        # everything scoring needs is pre-registered now, so scoring never reads the expiring bundle
        config = build_config(self.settings, bundle, run_id, code_version(), view.baselines, canary_passed)
        system = system_specs(
            output, config.k_per_type, manifest.channel_ids, await self._drafts(run_id, config)
        )
        universe = [c for c in manifest.channel_ids if c != manifest.own_channel_id]
        specs = generate_arms(
            ArmContext(
                system=system.specs,
                window=view.window,
                topic_ref_ids=[ref.id for ref in bundle.snapshot.topic_refs],
                universe=universe,
                replicates=config.random_replicates,
                seed=config.seed,
            )
        )
        eval_id = eval_id_for(run_id)
        async with self.session_factory() as session, session.begin():
            store = BacktestStore(session)
            await store.register(eval_id, config, now)
            await store.replace_specs(eval_id, config, specs, manifest.expires_at)
        return RecordSummary(
            eval_id=eval_id,
            specs_by_arm=dict(Counter(s.arm for s in specs)),
            outcomes_mature_at=config.outcomes_mature_at(),
            notes=system.notes,
        )

    async def _drafts(self, run_id: uuid.UUID, config: BacktestConfig) -> dict[str, RecommendationDraft]:
        statement = select(RecommendationRow.stable_key, RecommendationRow.draft).where(
            RecommendationRow.workspace_id == config.workspace_id,
            RecommendationRow.client_id == config.client_id,
            RecommendationRow.run_id == run_id,
            RecommendationRow.draft.is_not(None),
        )
        async with self.session_factory() as session:
            rows = (await session.execute(statement)).all()
        return {key: RecommendationDraft.model_validate(draft) for key, draft in rows}


class BacktestRunner:
    """scores registered evaluations from their pre-registered config and the live store at E; it reads no
    bundle and spends no quota or llm budget."""

    def __init__(self, container: Any) -> None:
        self.session_factory = container.session_factory
        self.settings: Settings = container.settings
        self.input_source = container.adapter("analytics_inputs")

    async def score(self, eval_ids: list[uuid.UUID], now: datetime) -> BacktestReport:
        engine = engine_for(self.settings)
        evaluations, configs, freezes = [], [], []
        for eval_id in eval_ids:
            async with self.session_factory() as session:
                store = BacktestStore(session)
                config = await store.config(eval_id)
                specs = await store.specs(eval_id)
                freeze = await FreezeRepository(session).get(config.freeze_id)
            if now < config.outcomes_mature_at():
                raise OutcomesNotMature(
                    f"evaluation {eval_id}: outcomes mature at {config.outcomes_mature_at().isoformat()}"
                )
            evaluations.append(await self._evaluate(eval_id, config, specs, engine, now))
            configs.append(config)
            freezes.append(
                {
                    "freeze_id": str(freeze.freeze_id),
                    "client_id": str(freeze.client_id),
                    "as_of": freeze.as_of.isoformat(),
                    "pit_level": config.pit_level,
                    "late_tables": list(config.late_tables),
                    "channels": len(config.universe),
                    "record_counts": dict(freeze.record_counts),
                }
            )
        frame = build_frame(evaluations)
        report = score(
            evaluations,
            configs,
            freezes,
            scoring_settings(configs, self.settings.evaluation.backtest.few_clusters),
            now,
            frame,
        )
        matched = matched_arms(frame)
        expires = now + timedelta(days=self.settings.policy.retention_days)
        async with self.session_factory() as session, session.begin():
            store = BacktestStore(session)
            for evaluation in evaluations:
                eval_id = uuid.UUID(evaluation.eval_id)
                await store.save_outcomes(eval_id, evaluation.outcomes, matched, expires)
                await store.save_results(eval_id, report)
                await store.mark(eval_id, "scored", finished_at=now)
        return report

    async def _evaluate(
        self, eval_id: uuid.UUID, config: BacktestConfig, specs: list[ArmSpec], engine: Any, now: datetime
    ) -> ClientEvaluation:
        live = await self.input_source.load(analysis_request(config.snapshot, now))
        result = await asyncio.to_thread(engine.run, live, now)
        outcomes = collect_outcomes(
            config,
            FrozenBaselines.from_json(config.frozen_baselines),
            live,
            result,
            config.universe,
            config.own_channel_id,
        )
        notes = []
        if config.late_tables:
            notes.append(
                f"approximate freeze for {config.client_id}: late tables {', '.join(config.late_tables)}"
            )
        return ClientEvaluation(
            eval_id=str(eval_id),
            client_id=str(config.client_id),
            specs=specs,
            outcomes=outcomes,
            replicates=config.random_replicates,
            notes=notes,
            invalid_reasons=[] if config.leakage_canary_passed else ["leakage canary failed at record time"],
        )
