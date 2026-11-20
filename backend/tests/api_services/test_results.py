import hashlib
import uuid
from datetime import timedelta

import pytest

from app.api.services import RECOMMENDATIONS
from app.core.config import RecommendSettings
from app.domain.card_copy import CardCopy
from app.domain.confidence import ConfidenceBasis
from app.domain.recommendations import NewsSubject, RecType
from app.domain.refresh import RefreshStatus
from app.pipeline.stages.analytics import ANALYTICS_RESULT
from app.pipeline.stages.cards import WRITTEN_CARDS
from app.pipeline.stages.recommend import RECOMMEND_ARTIFACT
from app.repositories.shared.youtube import ChannelRepository, VideoRepository
from app.services.analytics.engine import AnalyticsEngine
from app.services.analytics.settings import AnalyticsEngineSettings
from app.services.llm.writer.schemas import WrittenCard, WrittenCards
from app.services.news.repository import NewsSignalStore
from app.services.news.scanner import StoredJudgement
from app.services.recommend.repository import RecommendationRepository
from app.services.recommend.service import RecommendationService
from app.services.refresh.store import DescriptionRefreshRepository
from app.services.website_intel.persistence import WebsiteIntelStore
from tests.analytics.apollo_fixture import APOLLO, COMPETITORS
from tests.analytics.factories import AS_OF as ANALYTICS_AS_OF
from tests.analytics.test_golden import build_inputs
from tests.api.conftest import Harness
from tests.api_services.conftest import Tenant, finished_run, publish, put_artifact, tenant
from tests.recommend.fixtures import GENERATED_AT, apollo_inputs, video_id
from tests.refresh.fixtures import news_signal

pytestmark = pytest.mark.db

DESCRIPTION = "Book: https://example.com/book\nKnee pain causes"


@pytest.fixture(scope="module")
def analytics_result():
    # the full per-video maps exceed the 2 MB artifact cap, so the fixture keeps one channel's videos
    result = AnalyticsEngine(AnalyticsEngineSettings()).run(build_inputs(), ANALYTICS_AS_OF)
    kept = {
        age: {vid: m for vid, m in by_video.items() if m.channel_id == COMPETITORS[0]}
        for age, by_video in result.metrics.items()
    }
    wanted = {vid for by_video in kept.values() for vid in by_video}
    return result.model_copy(
        update={
            "metrics": kept,
            "traits": {vid: traits for vid, traits in result.traits.items() if vid in wanted},
            "comment_signals": {},
        }
    )


async def published(harness: Harness, label: str, analytics_result) -> tuple[Tenant, uuid.UUID, object]:
    owner = await tenant(harness, label)
    run_id = await finished_run(harness, owner.scope)
    inputs = apollo_inputs()
    run = await RecommendationService(RecommendSettings()).generate(
        inputs, run_id=run_id, generated_at=GENERATED_AT
    )
    await put_artifact(harness, owner.scope, run_id, ANALYTICS_RESULT, analytics_result)
    await put_artifact(harness, owner.scope, run_id, RECOMMEND_ARTIFACT, run.output)
    async with harness.container.uow() as uow:
        questions = [
            question.model_copy(update={"client_id": owner.scope.client_id})
            for question in inputs.buyer_questions
        ]
        await WebsiteIntelStore(uow.session, owner.scope).save_questions(questions)
        await uow.repository(RecommendationRepository, owner.scope).save_run(run_id, run)
        await uow.commit()
    await publish(harness, owner.scope, run_id)
    return owner, run_id, run


async def test_analytics_sheets_pass_raw_numbers_with_display_names(
    harness: Harness, analytics_result
) -> None:
    owner, _, _ = await published(harness, "sheets", analytics_result)
    async with harness.container.session_factory() as session, session.begin():
        await ChannelRepository(session).upsert_many(
            [
                {
                    "channel_id": COMPETITORS[0],
                    "title": "Rival One",
                    "metadata_fetched_at": ANALYTICS_AS_OF,
                    "stats_fetched_at": ANALYTICS_AS_OF,
                }
            ]
        )
    sheets = await harness.client.get(
        f"{owner.path}/analytics/competitors", params={"sort": "-uploads_per_week"}, headers=owner.headers
    )
    assert sheets.status_code == 200, sheets.text
    data = sheets.json()["data"]
    assert data and all("score" not in sheet["scorecard"] for sheet in data)
    competitors = [sheet for sheet in data if sheet["scorecard"]["role"] == "competitor"]
    weekly = [sheet["scorecard"]["uploads_per_week"] for sheet in competitors]
    assert weekly == sorted(weekly, reverse=True)
    titled = {sheet["channel"]["channel_id"]: sheet["channel"]["title"] for sheet in data}
    assert titled[COMPETITORS[0]] == "Rival One"
    expected = {card.channel_id: card for card in analytics_result.windows[30].scorecards}
    assert all(
        sheet["scorecard"]["median_ratio"] == expected[sheet["channel"]["channel_id"]].median_ratio
        for sheet in data
    )
    bad = await harness.client.get(
        f"{owner.path}/analytics/competitors", params={"sort": "score"}, headers=owner.headers
    )
    assert bad.status_code == 400

    detail = await harness.client.get(
        f"{owner.path}/analytics/competitors/{COMPETITORS[0]}", headers=owner.headers
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["scorecard"]["channel_id"] == COMPETITORS[0]
    assert (
        await harness.client.get(f"{owner.path}/analytics/competitors/UC{'z' * 22}", headers=owner.headers)
    ).status_code == 404

    videos = await harness.client.get(
        f"{owner.path}/analytics/competitors/{COMPETITORS[0]}/videos",
        params={"limit": 2},
        headers=owner.headers,
    )
    page = videos.json()["data"]
    assert len(page["items"]) <= 2 and all(
        row["metrics"]["channel_id"] == COMPETITORS[0] for row in page["items"]
    )
    if page["next_cursor"]:
        following = await harness.client.get(
            f"{owner.path}/analytics/competitors/{COMPETITORS[0]}/videos",
            params={"limit": 2, "cursor": page["next_cursor"]},
            headers=owner.headers,
        )
        assert following.status_code == 200
        mismatch = await harness.client.get(
            f"{owner.path}/analytics/competitors/{COMPETITORS[0]}/videos",
            params={"limit": 2, "cursor": page["next_cursor"], "sort": "published_at"},
            headers=owner.headers,
        )
        assert mismatch.status_code == 400

    outliers = await harness.client.get(
        f"{owner.path}/analytics/outliers", params={"min_ratio": 3}, headers=owner.headers
    )
    rows = outliers.json()["data"]["items"]
    assert rows and all(row["outlier"]["ratio"] >= 3 for row in rows)
    assert rows[0]["video"]["thumbnail_url"].startswith("https://i.ytimg.com/vi/")

    own = (await harness.client.get(f"{owner.path}/analytics/own-performance", headers=owner.headers)).json()[
        "data"
    ]
    assert own["youtube_public"]["channel_id"] == APOLLO and own["website"] is None
    patterns = await harness.client.get(f"{owner.path}/analytics/patterns", headers=owner.headers)
    assert patterns.status_code == 200
    trending = (await harness.client.get(f"{owner.path}/analytics/trending", headers=owner.headers)).json()[
        "data"
    ]
    assert trending["report"]["window_days"] == 30
    topics = await harness.client.get(f"{owner.path}/analytics/topics", headers=owner.headers)
    assert topics.status_code == 200


async def test_recommendations_overlay_written_copy_and_honour_feedback(
    harness: Harness, analytics_result
) -> None:
    owner, run_id, run = await published(harness, "cards", analytics_result)
    cards = run.output.window(30).set.recommendations
    first = cards[0]
    copy = CardCopy(
        draft_id=f"d_{first.extras.stable_key[:16]}",
        summary="Film a knee recovery explainer this week",
        why_it_works=["Three rival recovery videos ran far above their own channel's normal"],
        fit_to_client="You offer knee replacement in Chennai and Delhi",
        reason_fact_ids=[["F0001"]],
        fit_fact_ids=["F0002"],
        copy_source="llm",
        prompt_id="card_write",
        prompt_version="1",
        attempts=1,
        language="en",
    )
    written = WrittenCards(
        cards=[WrittenCard(draft_id=copy.draft_id, stable_key=first.extras.stable_key, card_copy=copy)],
        llm_cards=1,
    )
    await put_artifact(harness, owner.scope, run_id, WRITTEN_CARDS, written)

    full = await harness.client.get(f"{owner.path}/recommendations", headers=owner.headers)
    assert full.status_code == 200, full.text
    items = full.json()["data"]["items"]
    assert len(items) == len(cards)
    overlaid = next(item for item in items if item["recommendation"]["id"] == first.id)["recommendation"]
    assert overlaid["summary"] == copy.summary and overlaid["extras"]["rendered_by"] == "llm"
    assert overlaid["reference_videos"] == first.model_dump(mode="json")["reference_videos"]
    etag = full.headers["etag"]
    assert (
        await harness.client.get(
            f"{owner.path}/recommendations", headers={**owner.headers, "If-None-Match": etag}
        )
    ).status_code == 304

    strict = await harness.client.get(
        f"{owner.path}/recommendations", params={"format": "contract"}, headers=owner.headers
    )
    contract = strict.json()["data"]["items"][0]
    assert set(contract) == {
        "id",
        "type",
        "summary",
        "lens",
        "buyer_question",
        "why_it_works",
        "reference_videos",
        "fit_to_client",
        "expected_range_vs_baseline",
        "confidence",
        "window_days",
        "generated_at",
    }
    typed = await harness.client.get(
        f"{owner.path}/recommendations", params={"type": "creator"}, headers=owner.headers
    )
    assert all(item["recommendation"]["type"] == "creator" for item in typed.json()["data"]["items"])

    # the bootstrap key was minted by a user, so feedback is recorded against that user
    user_headers = owner.headers
    recorded = await harness.client.put(
        f"/api/v1/recommendations/{first.id}/feedback",
        json={"status": "dismissed", "reason": "already_covered"},
        headers=user_headers,
    )
    assert recorded.status_code == 200, recorded.text
    assert recorded.json()["feedback"]["status"] == "dismissed"
    bad_reason = await harness.client.put(
        f"/api/v1/recommendations/{first.id}/feedback",
        json={"status": "saved", "reason": "other"},
        headers=user_headers,
    )
    assert bad_reason.status_code == 422
    visible = (await harness.client.get(f"{owner.path}/recommendations", headers=user_headers)).json()[
        "data"
    ]["items"]
    assert first.id not in {item["recommendation"]["id"] for item in visible}
    dismissed = await harness.client.get(
        f"{owner.path}/recommendations", params={"status": "dismissed"}, headers=user_headers
    )
    assert [item["recommendation"]["id"] for item in dismissed.json()["data"]["items"]] == [first.id]

    single = await harness.client.get(f"/api/v1/recommendations/{first.id}", headers=user_headers)
    assert (
        single.json()["recommendation"]["summary"] == copy.summary
        and single.json()["feedback"]["status"] == "dismissed"
    )

    service = harness.state.service(RECOMMENDATIONS)
    assert await service.cards_written(owner.scope, run_id, 30)
    unwritten = [
        window.set.window_days
        for window in run.output.windows
        if window.set.recommendations and window.set.window_days != 30
    ]
    assert unwritten and not any(
        [await service.cards_written(owner.scope, run_id, window) for window in unwritten]
    )


async def test_creators_news_early_signals_and_refresh(harness: Harness, analytics_result) -> None:
    owner, run_id, run = await published(harness, "surfaces", analytics_result)
    creators = await harness.client.get(
        f"{owner.path}/creators", params={"sort": "uploads_90d"}, headers=owner.headers
    )
    assert creators.status_code == 200, creators.text
    data = creators.json()["data"]
    assert data["sort"] == "uploads_90d" and "uploads_90d" in data["available_sorts"]
    groups = [item["group"] for item in data["items"]]
    assert groups == sorted(groups, key=lambda group: group != "strong")
    for group in ("strong", "related"):
        values = [item["sort_value"] for item in data["items"] if item["group"] == group]
        assert values == sorted(values, reverse=True)
    assert all("score" not in item for item in data["items"])
    assert (
        await harness.client.get(f"{owner.path}/creators", params={"sort": "score"}, headers=owner.headers)
    ).status_code == 422

    window = run.output.window(run.output.selected_window)
    news_cards = [card for card in window.set.recommendations if card.type is RecType.news_opportunity]
    signal_ids = {
        card.extras.subject.news_signal_id
        for card in news_cards
        if isinstance(card.extras.subject, NewsSubject)
    }
    signal_ids |= {item.news_signal_id for item in run.output.window(30).early_signals}
    async with harness.container.session_factory() as session, session.begin():
        store = NewsSignalStore(session, owner.scope)
        for index, signal_id in enumerate(sorted(signal_ids)):
            signal = news_signal(f"Fable 5.{index}", topic_ref_ids=[]).model_copy(
                update={
                    "id": signal_id,
                    "client_id": owner.scope.client_id,
                    "first_seen_at": harness.container.clock.now(),
                    "created_at": harness.container.clock.now(),
                    "expires_at": harness.container.clock.now() + timedelta(days=5),
                }
            )
            await store.save_judgements(
                [
                    StoredJudgement(
                        signal.story_id, 1, signal.judgement, [], [], 3, signal.credibility.best_tier, "test"
                    )
                ]
            )
            await store.save_signals([signal], harness.container.clock.now())

    opportunities = await harness.client.get(f"{owner.path}/news/opportunities", headers=owner.headers)
    assert opportunities.status_code == 200, opportunities.text
    views = opportunities.json()["data"]["items"]
    assert len(views) == len(news_cards)
    signals = await harness.client.get(f"{owner.path}/news/signals", headers=owner.headers)
    assert len(signals.json()["items"]) == len(signal_ids)
    early = (await harness.client.get(f"{owner.path}/early-signals", headers=owner.headers)).json()["data"]
    assert len(early) == len(run.output.window(30).early_signals)
    if early:
        dismissed = await harness.client.post(
            f"/api/v1/news/opportunities/{early[0]['opportunity_id']}/dismiss", headers=owner.headers
        )
        assert dismissed.status_code == 200 and dismissed.json()["status"] == "dismissed"
        again = (await harness.client.get(f"{owner.path}/early-signals", headers=owner.headers)).json()[
            "data"
        ]
        assert early[0]["id"] not in {item["id"] for item in again}

    await seed_refreshes(harness, owner, run_id)
    listed = (await harness.client.get(f"{owner.path}/refresh-suggestions", headers=owner.headers)).json()[
        "data"
    ]["items"]
    assert [item["basis"] for item in listed] == ["keyword"]
    suggestion = listed[0]
    assert suggestion["description_changed"] is False and "+" in suggestion["preview_diff"]
    hygiene = (await harness.client.get(f"{owner.path}/hygiene", headers=owner.headers)).json()["data"]
    assert [item["basis"] for item in hygiene["items"]] == ["rule_based"]

    rejected = await harness.client.post(
        f"/api/v1/refresh-suggestions/{suggestion['id']}/applied",
        json={"applied_text": "something else"},
        headers=owner.headers,
    )
    assert rejected.status_code == 422
    applied = await harness.client.post(
        f"/api/v1/refresh-suggestions/{suggestion['id']}/applied", json={}, headers=owner.headers
    )
    assert (
        applied.status_code == 200 and applied.json()["status"] == "applied" and applied.json()["applied_at"]
    )
    twice = await harness.client.post(
        f"/api/v1/refresh-suggestions/{suggestion['id']}/dismiss", headers=owner.headers
    )
    assert twice.status_code == 409
    fix = hygiene["items"][0]["id"]
    assert (
        await harness.client.post(f"/api/v1/refresh-suggestions/{fix}/dismiss", headers=owner.headers)
    ).json()["status"] == "dismissed"
    other = await tenant(harness, "outsider")
    assert (
        await harness.client.post(f"/api/v1/refresh-suggestions/{fix}/dismiss", headers=other.headers)
    ).status_code == 404


async def seed_refreshes(harness: Harness, owner: Tenant, run_id: uuid.UUID) -> None:
    details = apollo_inputs().refreshes[0]
    now = harness.container.clock.now()
    keyword = details.model_copy(
        update={
            "id": str(uuid.uuid4()),
            "client_id": owner.scope.client_id,
            "run_id": run_id,
            "status": RefreshStatus.proposed,
            "description_hash_at_generation": hashlib.sha1(DESCRIPTION.encode()).hexdigest(),
            "expires_at": now + timedelta(days=14),
        }
    )
    rule = keyword.model_copy(
        update={
            "id": str(uuid.uuid4()),
            "video_id": video_id("o3"),
            "confidence": keyword.confidence.model_copy(update={"basis": ConfidenceBasis.rule_based}),
        }
    )
    async with harness.container.session_factory() as session, session.begin():
        await ChannelRepository(session).upsert_many(
            [{"channel_id": APOLLO, "metadata_fetched_at": now, "stats_fetched_at": now}]
        )
        await VideoRepository(session).upsert_many(
            [
                {
                    "video_id": keyword.video_id,
                    "channel_id": APOLLO,
                    "published_at": now - timedelta(days=200),
                    "effective_published_at": now - timedelta(days=200),
                    "title": "Knee replacement explained",
                    "description": DESCRIPTION,
                    "description_hash": keyword.description_hash_at_generation,
                    "ingest_source": "uploads",
                    "metadata_fetched_at": now,
                    "stats_fetched_at": now,
                }
            ]
        )
        repository = DescriptionRefreshRepository(session, owner.scope)
        repository.save(keyword)
        repository.save(rule)
