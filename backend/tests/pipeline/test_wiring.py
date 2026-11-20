import uuid
from datetime import UTC, datetime, timedelta

from arq.worker import get_kwargs
from pydantic import BaseModel
from sqlalchemy import insert, select

from app.bootstrap import load_registrations
from app.domain.profile import ClaimStatus
from app.domain.topics import TopicKind
from app.models.runs import PipelineRun, RunArtifact, RunArtifactBlob
from app.pipeline.contracts import Market, RunConfigSnapshot
from app.pipeline.definitions import ANALYSIS_STAGES, ONBOARDING_STAGES
from app.pipeline.inputs.common import artifact_store
from app.pipeline.registry import validate_pipelines
from app.pipeline.setup_confirmer import accept_claims
from app.pipeline.stages.niche import niche_query
from app.pipeline.stages.topic_seed import keyword_rows
from app.ports.artifacts import ArtifactKey
from app.workers.settings import BatchWorkerSettings, InteractiveWorkerSettings, MaintenanceWorkerSettings
from tests.competitors.apollo import apollo_profile
from tests.refresh.fixtures import topic_ref


def make_snapshot(**overrides) -> RunConfigSnapshot:
    values = {
        "workspace_id": uuid.uuid4(),
        "client_id": uuid.uuid4(),
        "config_version": 1,
        "config_hash": "h",
        "window_days": 30,
        "topic_set_version": 1,
        "market": Market(country="IN", language="en-IN", timezone="Asia/Kolkata"),
        **overrides,
    }
    return RunConfigSnapshot(**values)


def test_every_pipeline_is_runnable_after_bootstrap():
    report = load_registrations()
    catalog = validate_pipelines(require_complete=True)
    assert not report.missing
    assert {"onboarding", "analysis", "daily_refresh", "cards", "news_scan", "recrawl"} <= set(
        catalog.available
    )
    assert set(ONBOARDING_STAGES) | set(ANALYSIS_STAGES) <= set(catalog.stages)


def test_backtest_replays_without_the_card_writer():
    load_registrations()
    catalog = validate_pipelines(require_complete=True)
    # wording is irrelevant to the backtest and its cards are never persisted
    assert "write_cards" not in catalog.require("backtest").stages
    assert catalog.require("backtest").resolve("finalize") == "record_backtest"


def test_recommend_reads_news_and_refresh_drafts_first():
    load_registrations()
    catalog = validate_pipelines()
    assert set(catalog.stage("recommend").optional_deps) == {"news", "refresh"}


def test_arq_sees_the_shared_worker_options():
    # arq reads only each settings class's own __dict__
    for worker in (InteractiveWorkerSettings, BatchWorkerSettings, MaintenanceWorkerSettings):
        options = get_kwargs(worker)
        assert {"on_startup", "on_shutdown", "redis_settings", "max_tries"} <= set(options)
    assert get_kwargs(MaintenanceWorkerSettings)["cron_jobs"]
    assert not get_kwargs(InteractiveWorkerSettings)["cron_jobs"]


def test_accept_claims_confirms_proposed_items_only():
    tree = {
        "offerings": [
            {"name": "a", "meta": {"origin": "extracted", "status": "proposed"}},
            {"name": "b", "meta": {"origin": "extracted", "status": "rejected"}},
            {"name": "c", "meta": {"origin": "derived", "status": "needs_confirmation"}},
        ],
        "notes": {"meta": {"status": "proposed"}},
    }
    accepted = accept_claims(tree)
    # third-party claims that need confirmation are never accepted on the user's behalf
    assert [item["meta"]["status"] for item in accepted["offerings"]] == [
        "confirmed",
        "rejected",
        "needs_confirmation",
    ]
    # only claim metadata (it carries an origin) is touched
    assert accepted["notes"]["meta"]["status"] == "proposed"


def test_profile_keywords_become_proposed_rows_without_rejected_ones():
    profile = apollo_profile()
    rows = keyword_rows(profile, profile.client_id)
    usable = [
        k for k in profile.niche_keywords if k.meta.status not in {ClaimStatus.rejected, ClaimStatus.stale}
    ]
    assert len(rows) == len({(k.term.casefold().strip(), k.language) for k in usable})
    assert all(row["status"] in {"proposed", "confirmed"} for row in rows)
    assert all(row["introduced_in_profile_id"] == profile.profile_id for row in rows)


def test_niche_query_rotates_offerings_daily_and_skips_questions_and_acronyms():
    knee = topic_ref("Knee replacement", TopicKind.offering)
    heart = topic_ref("Cardiac sciences", TopicKind.offering)
    ent = topic_ref("ENT", TopicKind.offering)
    keyword = topic_ref("best hospital near me")
    question = topic_ref("how much does a knee replacement cost?", TopicKind.buyer_question)
    snapshot = make_snapshot(topic_refs=[knee, heart, ent, keyword, question])
    day = datetime(2026, 11, 20, tzinfo=UTC)
    picked = {niche_query(snapshot, day + timedelta(days=offset)) for offset in range(4)}
    assert picked == {"Knee replacement", "Cardiac sciences"}
    assert niche_query(make_snapshot(topic_refs=[keyword, question]), day) == "best hospital near me"
    assert niche_query(make_snapshot(topic_refs=[question]), day) is None


class Bulky(BaseModel):
    rows: list[str]


BULKY = ArtifactKey(name="bulky", model=Bulky, schema_version=1, contains_api_data=True)


async def test_oversized_artifacts_are_stored_as_blobs_and_read_back(container, tenant):
    store = artifact_store(container, tenant)
    async with container.session_factory() as session, session.begin():
        run_id = (
            await session.execute(
                insert(PipelineRun)
                .values(
                    workspace_id=tenant.workspace_id,
                    client_id=tenant.client_id,
                    kind="analysis",
                    trigger="user",
                    status="running",
                    config_snapshot={},
                    config_hash="h",
                    queue="interactive",
                    budget={},
                    as_of=container.clock.now(),
                )
                .returning(PipelineRun.id)
            )
        ).scalar_one()
    value = Bulky(rows=[f"{index:08d}" * 40 for index in range(10_000)])
    await store.put(run_id, BULKY, value, "fp")
    async with container.session_factory() as session:
        row = (await session.execute(select(RunArtifact).where(RunArtifact.run_id == run_id))).scalar_one()
        blobs = (await session.execute(select(RunArtifactBlob))).scalars().all()
    assert set(row.payload) == {"$blob", "bytes"}
    assert [blob.sha256 for blob in blobs] == [row.payload["$blob"]]
    assert await store.get(run_id, BULKY) == value
    assert (await store.find_latest(BULKY)).value == value


async def test_niche_search_hits_are_baselined_as_creators():
    from types import SimpleNamespace

    from app.domain.common import ChannelRole
    from app.pipeline.stages.niche import (
        CREATOR_BASELINES,
        NicheChannel,
        NicheDiscovery,
        NicheDiscoveryStage,
        creator_channel_ids,
    )
    from app.services.youtube.ingest import ChannelIngestResult

    asked: list[str] = []

    async def ensure_baseline(channel_ids, request):
        asked.extend(channel_ids)
        return [ChannelIngestResult(channel_id=cid) for cid in channel_ids]

    layer = SimpleNamespace(ingest=SimpleNamespace(ensure_baseline=ensure_baseline))
    hits = {f"UC{index:022d}": 20 - index for index in range(CREATOR_BASELINES + 3)}
    tracked_id = next(iter(hits))
    artifact = NicheDiscovery(
        channels=[NicheChannel(channel_id=tracked_id, role=ChannelRole.niche, status="ok")], search_hits=hits
    )
    notices: list = []
    result = await NicheDiscoveryStage._baseline_creators(None, layer, None, artifact, {tracked_id}, notices)
    assert tracked_id not in asked and len(asked) == CREATOR_BASELINES
    assert asked == sorted(asked, key=lambda cid: -hits[cid])
    assert creator_channel_ids(result) == asked and not notices
