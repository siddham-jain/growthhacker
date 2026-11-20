import uuid

from app.core.clock import FrozenClock
from app.core.config import CompetitorSettings
from app.core.registry import stage_registry
from app.domain.runs import RunKind, RunTrigger
from app.models.runs import PipelineRun
from app.pipeline.contracts import RunConfigSnapshot, RunContext
from app.pipeline.stages.competitors import (
    BUSINESS_PROFILE_ARTIFACT,
    CompetitorDriftStage,
    DiscoverCompetitorsStage,
)
from tests.competitors.apollo import (
    EXPECTED_COMPETITORS,
    apollo_crawler,
    apollo_llm,
    apollo_profile,
    apollo_web_search,
    apollo_youtube,
)
from tests.competitors.fakes import NOW, MemoryLocks, RecordingReporter
from tests.contracts.test_pipeline_contracts import Token, UnlimitedLedger
from tests.foundation.factories import create_tenant, insert_row


class StageContainer:
    """the slice of the composition root the competitor stages use."""

    def __init__(self, session_factory, adapters):
        self.session_factory = session_factory
        self.lock_manager = MemoryLocks()
        self.clock = FrozenClock(NOW)
        self.settings = type("S", (), {"competitors": CompetitorSettings()})()
        self.adapters = adapters

    def adapter(self, port):
        if port not in self.adapters:
            raise LookupError(port)
        return self.adapters[port]


class ProfileArtifacts:
    async def get(self, key):
        assert key == BUSINESS_PROFILE_ARTIFACT
        return apollo_profile()

    async def get_optional(self, key):
        return None


async def pipeline_run(session_factory, scope) -> uuid.UUID:
    async with session_factory() as session:
        row = await insert_row(session, PipelineRun.__table__, scope, {"kind": "onboarding"})
        await session.commit()
    return row["id"]


def context(scope, container, run_id, competitor_ids=()):
    snapshot = RunConfigSnapshot(
        workspace_id=scope.workspace_id,
        client_id=scope.client_id,
        config_version=1,
        config_hash="h",
        window_days=30,
        topic_set_version=1,
        competitor_channel_ids=list(competitor_ids),
        market={"country": "IN", "language": "en", "timezone": "Asia/Kolkata"},
    )
    return RunContext(
        run_id=run_id,
        kind=RunKind.onboarding,
        trigger=RunTrigger.user,
        attempt=1,
        as_of=NOW,
        snapshot=snapshot,
        clock=FrozenClock(NOW),
        budget=UnlimitedLedger(),
        reporter=RecordingReporter(),
        artifacts=ProfileArtifacts(),
        cancel=Token(),
        deps=container,
    )


async def test_stages_are_registered():
    assert stage_registry.get("discover_competitors") is DiscoverCompetitorsStage
    assert stage_registry.get("competitor_drift") is CompetitorDriftStage


async def test_discover_then_drift_stage(session_factory):
    async with session_factory() as session:
        scope = await create_tenant(session, "apollo")
        await session.commit()
    adapters = {
        "youtube": apollo_youtube(),
        "web_search": apollo_web_search(),
        "site_crawler": apollo_crawler(),
        "llm": apollo_llm(),
    }
    container = StageContainer(session_factory, adapters)
    run_id = await pipeline_run(session_factory, scope)
    discovery = await DiscoverCompetitorsStage().run(context(scope, container, run_id))
    assert discovery.status == "succeeded"
    assert set(discovery.artifact.proposal.competitor_channel_ids) == EXPECTED_COMPETITORS
    assert discovery.counts["competitors_proposed"] == 5

    drift_stage = CompetitorDriftStage()
    ctx = context(scope, container, run_id, discovery.artifact.proposal.competitor_channel_ids)
    assert drift_stage.enabled(ctx.snapshot)
    drift = await drift_stage.run(ctx)
    assert drift.status == "succeeded" and drift.artifact.events == []


async def test_discovery_without_optional_adapters_degrades(session_factory):
    async with session_factory() as session:
        scope = await create_tenant(session, "apollo")
        await session.commit()
    container = StageContainer(session_factory, {"youtube": apollo_youtube()})
    result = await DiscoverCompetitorsStage().run(
        context(scope, container, await pipeline_run(session_factory, scope))
    )
    assert result.status in {"succeeded", "degraded"}
    assert not result.artifact.proposal.auto_confirmable
