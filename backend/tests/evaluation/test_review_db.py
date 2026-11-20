from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.api.errors import NotFound
from app.api.schemas.admin import ReviewIn
from app.core.clock import FrozenClock
from app.core.config import ReviewSettings
from app.domain.recommendations import RecommendationSet
from app.evaluation.packs.ids import batch_uuid
from app.evaluation.review.api import ReviewApi
from app.evaluation.review.sampler import ReviewSampleDesigner
from app.evaluation.review.source import PublishedCardSource
from app.evaluation.review.store import ReviewStore
from app.models.runs import ActiveResultSet, PipelineRun, RunArtifact
from app.models.youtube_cache import YtChannel, YtVideo
from app.pipeline.contracts import Market, RunConfigSnapshot
from app.pipeline.stages.recommend import RECOMMEND_ARTIFACT
from app.services.recommend.creators.ranker import CreatorList
from app.services.recommend.gaps import GapReport
from app.services.recommend.service import RecommendOutput, WindowOutput
from tests.conftest import make_settings
from tests.contracts.factories import make_recommendation
from tests.foundation.factories import create_tenant, insert_row

pytestmark = pytest.mark.db
NOW = datetime(2026, 11, 20, 12, tzinfo=UTC)
RIVAL = "UC" + "r" * 22


async def seed_published_run(session, scope) -> None:
    snapshot = RunConfigSnapshot(
        workspace_id=scope.workspace_id,
        client_id=scope.client_id,
        config_version=1,
        config_hash="h",
        window_days=30,
        competitor_channel_ids=[RIVAL],
        topic_set_version=1,
        market=Market(country="IN", language="en", timezone="Asia/Kolkata"),
    )
    run = await insert_row(
        session,
        PipelineRun.__table__,
        scope,
        {"kind": "analysis", "status": "succeeded", "config_snapshot": snapshot.model_dump(mode="json")},
    )
    rec = make_recommendation()
    output = RecommendOutput(
        client_id=scope.client_id,
        as_of=NOW,
        generated_at=NOW,
        selected_window=30,
        windows=[
            WindowOutput(
                set=RecommendationSet(
                    client_id=scope.client_id,
                    window_days=30,
                    as_of=NOW,
                    generated_at=NOW,
                    recommendations=[rec],
                ),
                creator_list=CreatorList(window_days=30, sort="matching_uploads", groups={}),
                gap_report=GapReport(window_days=30, as_of=NOW, rows=[]),
                writer="template",
            )
        ],
    )
    await insert_row(
        session,
        RunArtifact.__table__,
        scope,
        {
            "run_id": run["id"],
            "key": RECOMMEND_ARTIFACT.name,
            "schema_version": RECOMMEND_ARTIFACT.schema_version,
            "fingerprint": "f",
            "payload": output.model_dump(mode="json"),
            "contains_api_data": True,
            "api_data_observed_at": NOW - timedelta(hours=2),
            "expires_at": NOW + timedelta(days=28),
        },
    )
    await insert_row(
        session,
        ActiveResultSet.__table__,
        scope,
        {"window_days": 30, "run_id": run["id"], "published_at": NOW},
    )
    await insert_row(session, YtChannel.__table__, scope, {"channel_id": RIVAL, "title": "Rival Hospital"})
    for k in range(2):
        await insert_row(
            session,
            YtVideo.__table__,
            scope,
            {
                "video_id": f"rivalvid00{k}",
                "channel_id": RIVAL,
                "title": f"Rival hit {k}",
                "published_at": NOW - timedelta(days=5),
                "effective_published_at": NOW - timedelta(days=5),
                "view_count": 50_000 - k,
                "ingest_source": "uploads",
                "metadata_fetched_at": NOW - timedelta(hours=5),
                "stats_fetched_at": NOW - timedelta(hours=3),
            },
        )


async def test_published_cards_to_review_summary(session_factory) -> None:
    settings = make_settings()
    async with session_factory() as session, session.begin():
        scope = await create_tenant(session, "review", country="IN")
        await seed_published_run(session, scope)
    [client] = await PublishedCardSource(session_factory).load()
    assert client.sheet.competitors == ("Rival Hospital",)
    assert [v.title for v in client.universe] == ["Rival hit 0", "Rival hit 1"]
    assert client.oldest_api_at == NOW - timedelta(hours=5)
    assert len(client.cards) == 1 and client.cards[0].gate_features["confidence.outliers"] == 6.0

    design = ReviewSampleDesigner(ReviewSettings(decoys=2)).design([client], 1, 28, batch_uuid(NOW))
    assert sum(item.is_decoy for item in design.items) == 2
    async with session_factory() as session, session.begin():
        assert await ReviewStore(session).save_design(design) == 3

    container = SimpleNamespace(session_factory=session_factory, settings=settings, clock=FrozenClock(NOW))
    api = ReviewApi(container)
    queue = await api.review_queue(scope)
    assert queue["batch_id"] == str(design.batch_id) and len(queue["items"]) == 3
    assert "is_decoy" not in str(queue)
    for item in queue["items"]:
        for reviewer in ("Mira", "Marketer"):
            body = ReviewIn(
                recommendation_id=item["item_id"],
                reviewer=reviewer,
                specific=4,
                evidenced=5,
                relevant=4,
                actionable=4,
                not_obvious=4,
                would_ship=True,
            )
            await api.submit_review(scope, body)
    with pytest.raises(NotFound):
        await api.submit_review(scope, body.model_copy(update={"recommendation_id": "rec_missing"}))
    summary = await api.review_summary(scope)
    assert summary["n_items"] == 1 and summary["n_decoys"] == 2 and summary["n_raters"] == 2
    assert summary["overall_mean"] == pytest.approx(4.2)
