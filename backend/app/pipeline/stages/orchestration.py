from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel
from sqlalchemy import func, select, update

from app.core.registry import stage_registry
from app.domain.runs import ClientLifecycle, RunKind, RunNotice, RunNoticeCode
from app.models.competitors import CompetitorCandidate
from app.models.tenancy import Client
from app.models.website import BuyerQuestion, Keyword
from app.pipeline.config_source import ConfigInvalid
from app.pipeline.contracts import RunConfigSnapshot, RunContext, StagePolicy, StageResult
from app.ports.artifacts import ArtifactKey

MIN_COMPETITORS = 3
MAX_COMPETITORS = 10
VALIDATED_KINDS = frozenset({RunKind.analysis, RunKind.daily_refresh, RunKind.news_scan, RunKind.cards})


class RunConfigArtifact(BaseModel):
    config_hash: str
    window_days: int
    windows: list[int]
    competitors: int
    topic_refs: int


class OnboardingSummary(BaseModel):
    competitors_suggested: int
    keywords_proposed: int
    buyer_questions_proposed: int


RUN_CONFIG = ArtifactKey(
    name="run_config", model=RunConfigArtifact, schema_version=1, contains_api_data=False
)
ONBOARDING_SUMMARY = ArtifactKey(
    name="onboarding_summary", model=OnboardingSummary, schema_version=1, contains_api_data=False
)


def setup_problems(snapshot: RunConfigSnapshot) -> list[str]:
    problems = []
    competitors = len(snapshot.competitor_channel_ids)
    if not MIN_COMPETITORS <= competitors <= MAX_COMPETITORS:
        problems.append(
            f"competitors_out_of_range: {competitors} confirmed, {MIN_COMPETITORS}-{MAX_COMPETITORS} needed"
        )
    if not snapshot.topic_refs:
        problems.append("topic_refs_required: no active keyword or buyer question")
    return problems


@stage_registry.register("snapshot_config")
class SnapshotConfigStage:
    """re-validates the frozen run inputs before any quota is spent."""

    name = "snapshot_config"
    version = 1
    depends_on: tuple[str, ...] = ()
    optional_deps: tuple[str, ...] = ()
    produces = RUN_CONFIG
    policy = StagePolicy(timeout_s=30, max_attempts=1, reusable=False)
    weight = 1.0

    def enabled(self, snapshot: RunConfigSnapshot) -> bool:
        return True

    def fingerprint_inputs(self, ctx: RunContext[Any]) -> Mapping[str, Any]:
        return {"config_hash": ctx.snapshot.config_hash, "kind": ctx.kind.value}

    async def run(self, ctx: RunContext[Any]) -> StageResult[RunConfigArtifact]:
        snapshot = ctx.snapshot
        problems = setup_problems(snapshot)
        notices: list[RunNotice] = []
        if problems and ctx.kind is RunKind.analysis:
            raise ConfigInvalid("; ".join(problems))
        if problems and ctx.kind in VALIDATED_KINDS:
            notices.append(
                RunNotice(
                    code=RunNoticeCode.competitor_unavailable
                    if any(problem.startswith("competitors") for problem in problems)
                    else RunNoticeCode.stale_config,
                    message="Your setup changed since it was confirmed; review competitors and topics.",
                    detail={"problems": problems},
                )
            )
        artifact = RunConfigArtifact(
            config_hash=snapshot.config_hash,
            window_days=snapshot.window_days,
            windows=list(snapshot.windows_computed),
            competitors=len(snapshot.competitor_channel_ids),
            topic_refs=len(snapshot.topic_refs),
        )
        return StageResult(
            status="succeeded",
            artifact=artifact,
            counts={"competitors": artifact.competitors, "topic_refs": artifact.topic_refs},
            notices=tuple(notices),
        )


@stage_registry.register("finish_onboarding")
class FinishOnboardingStage:
    """counts what onboarding proposed and hands the client over for confirmation."""

    name = "finish_onboarding"
    version = 1
    depends_on: tuple[str, ...] = ("build_profile", "discover_competitors", "seed_topic_refs")
    optional_deps: tuple[str, ...] = ("prefetch_own_channel",)
    produces = ONBOARDING_SUMMARY
    policy = StagePolicy(timeout_s=30, max_attempts=2, reusable=False)
    weight = 2.0

    def enabled(self, snapshot: RunConfigSnapshot) -> bool:
        return True

    def fingerprint_inputs(self, ctx: RunContext[Any]) -> Mapping[str, Any]:
        return {"client_id": str(ctx.client_id)}

    async def run(self, ctx: RunContext[Any]) -> StageResult[OnboardingSummary]:
        async with ctx.deps.session_factory() as session, session.begin():
            summary = OnboardingSummary(
                competitors_suggested=await _count(session, CompetitorCandidate, ctx, "suggested"),
                keywords_proposed=await _count(session, Keyword, ctx, "proposed"),
                buyer_questions_proposed=await _count(session, BuyerQuestion, ctx, "proposed"),
            )
            await session.execute(
                update(Client)
                .where(
                    Client.id == ctx.client_id,
                    Client.workspace_id == ctx.workspace_id,
                    Client.lifecycle == ClientLifecycle.onboarding.value,
                )
                .values(lifecycle=ClientLifecycle.awaiting_confirmation.value, updated_at=func.now())
            )
        await ctx.reporter.step("onboarding.ready", awaiting_message(summary))
        return StageResult(status="succeeded", artifact=summary, counts=summary.model_dump())


def awaiting_message(summary: OnboardingSummary) -> str:
    return (
        f"Ready to review: {summary.competitors_suggested} competitors, "
        f"{summary.keywords_proposed} keywords, {summary.buyer_questions_proposed} buyer questions"
    )


async def _count(session, model, ctx: RunContext[Any], status: str) -> int:
    statement = select(func.count()).where(
        model.workspace_id == ctx.workspace_id, model.client_id == ctx.client_id, model.status == status
    )
    return int((await session.execute(statement)).scalar_one())
