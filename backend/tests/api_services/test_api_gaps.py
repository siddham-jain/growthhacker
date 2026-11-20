from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import update

import app.evaluation.api  # noqa: F401  (registers 12's evaluation service)
from app.adapters.llm.health import RouteHealth
from app.api.services import SETTINGS, YOUTUBE_OAUTH, OAuthOutcome
from app.api.services_impl import settings as settings_service
from app.models.runs import PipelineRun
from app.services.analytics.engine import AnalyticsEngine
from app.services.analytics.settings import AnalyticsEngineSettings
from tests.analytics.apollo_fixture import APOLLO, COMPETITORS
from tests.analytics.factories import AS_OF as ANALYTICS_AS_OF
from tests.analytics.test_golden import COST_QUESTION, KNEE, build_inputs
from tests.api.conftest import PASSWORD, Harness
from tests.api_services import test_results
from tests.api_services.conftest import tenant
from tests.api_services.test_results import published

pytestmark = pytest.mark.db

analytics_result = test_results.analytics_result

SECRET = "sk-test-not-a-real-key-5678"
LLM = "/api/v1/settings/llm"


@pytest.fixture(scope="module")
def with_own_videos():
    # keeps the client's and one rival's per-video metrics, inside the artifact size cap
    result = AnalyticsEngine(AnalyticsEngineSettings()).run(build_inputs(), ANALYTICS_AS_OF)
    wanted = {APOLLO, COMPETITORS[0]}
    kept = {
        age: {vid: m for vid, m in by_video.items() if m.channel_id in wanted}
        for age, by_video in result.metrics.items()
    }
    videos = {vid for by_video in kept.values() for vid in by_video}
    return result.model_copy(
        update={
            "metrics": kept,
            "traits": {vid: traits for vid, traits in result.traits.items() if vid in videos},
            "comment_signals": {},
        }
    )


async def test_competitor_detail_carries_the_clients_own_figures(harness: Harness, with_own_videos) -> None:
    owner, _, _ = await published(harness, "you", with_own_videos)
    detail = await harness.client.get(
        f"{owner.path}/analytics/competitors/{COMPETITORS[0]}", headers=owner.headers
    )
    assert detail.status_code == 200, detail.text
    own = detail.json()["data"]["own"]
    window = with_own_videos.windows[30]
    since = with_own_videos.as_of - timedelta(days=30)
    mine = [
        m
        for m in with_own_videos.metrics[window.comparison_age_days].values()
        if m.channel_id == APOLLO and since <= m.age_anchor_at <= with_own_videos.as_of
    ]
    assert mine, "the fixture needs own videos in the window"
    card = own["scorecard"]
    assert own["channel"]["channel_id"] == APOLLO and own["channel"]["verified"]
    assert card["role"] == "own" and card["uploads_in_window"] == len(mine)
    assert card["outlier_hit_rate"] == [
        sum(m.outlier.is_outlier for m in mine if m.outlier),
        sum(1 for m in mine if m.outlier),
    ]
    assert "score" not in card and "rank" not in card
    assert sum(card["segment_mix"].values()) == pytest.approx(1)


async def test_gap_matrix_lists_buyer_questions_against_competitors(
    harness: Harness, analytics_result
) -> None:
    owner, run_id, _ = await published(harness, "gaps", analytics_result)
    async with harness.container.session_factory() as session, session.begin():
        run = await session.get(PipelineRun, run_id)
        snapshot = {
            **run.config_snapshot,
            "topic_refs": [ref.model_dump(mode="json") for ref in (KNEE, COST_QUESTION)],
        }
        await session.execute(
            update(PipelineRun).where(PipelineRun.id == run_id).values(config_snapshot=snapshot)
        )
    response = await harness.client.get(
        f"{owner.path}/analytics/gaps", params={"window_days": "30"}, headers=owner.headers
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert [row["topic_ref_id"] for row in data["rows"]] == [COST_QUESTION.id]
    assert {channel["channel_id"] for channel in data["channels"]} <= set(COMPETITORS)
    assert APOLLO not in {channel["channel_id"] for channel in data["channels"]}
    assert data["own_channel"]["channel_id"] == APOLLO
    source = analytics_result.windows[30].gap_matrix.cells.get(COST_QUESTION.id, {})
    for channel_id, cell in data["cells"].get(COST_QUESTION.id, {}).items():
        assert cell["n_videos"] == source[channel_id].n_videos
    assert all("score" not in cell for row in data["cells"].values() for cell in row.values())
    wrong = await harness.client.get(
        f"{owner.path}/analytics/gaps", params={"window_days": "14"}, headers=owner.headers
    )
    assert wrong.status_code == 422


async def test_notification_settings_default_and_merge(harness: Harness) -> None:
    owner = await tenant(harness, "notify")
    path = f"{owner.path}/notification-settings"
    initial = (await harness.client.get(path, headers=owner.headers)).json()
    assert initial["email"] is None and initial["first_brief_ready"] and initial["delivery_enabled"] is False
    saved = await harness.client.patch(
        path, json={"email": "ops@apollo.example", "daily_digest": True}, headers=owner.headers
    )
    assert saved.status_code == 200, saved.text
    again = (
        await harness.client.patch(path, json={"news_closing_soon": False}, headers=owner.headers)
    ).json()
    assert again["email"] == "ops@apollo.example" and again["daily_digest"] and not again["news_closing_soon"]
    bad = await harness.client.patch(path, json={"email": "not-an-email"}, headers=owner.headers)
    assert bad.status_code == 422
    other = await tenant(harness, "notify2")
    assert (
        await harness.client.get(f"{owner.path}/notification-settings", headers=other.headers)
    ).status_code == 404


async def test_llm_settings_customise_every_field_per_provider(harness: Harness) -> None:
    owner = await tenant(harness, "llmfull")
    body = {
        "provider_order": ["openai_compat", "anthropic"],
        "providers": {
            "anthropic": {
                "tiers": {"high": {"model": "claude-opus-5-5", "max_tokens": 12000, "effort": "high"}},
                "max_concurrency": 3,
            },
            "openai_compat": {
                "base_url": "https://gateway.example.com/v1",
                "api_key": SECRET,
                "tiers": {"low": {"model": "small", "timeout_s": 15, "max_retries": 3}},
                "extra_params": {"temperature": 0.2},
                "extra_body": {"provider": {"sort": "price"}},
                "token_param": "max_tokens",
                "structured_dialect": "openai_json_object",
            },
        },
    }
    saved = await harness.client.put(LLM, json=body, headers=owner.headers)
    assert saved.status_code == 200, saved.text
    assert SECRET not in saved.text
    view = saved.json()["providers"]
    assert view["anthropic"]["tiers"]["high"] == {
        "model": "claude-opus-5-5",
        "max_tokens": 12000,
        "timeout_s": 60.0,
        "max_retries": 1,
        "effort": "high",
    }
    assert view["anthropic"]["openai"] is None and view["anthropic"]["max_concurrency"] == 3
    assert view["openai_compat"]["openai"]["extra_params"] == {"temperature": 0.2}
    assert "tiers.low.model" in view["openai_compat"]["overridden"]

    effective = await harness.state.service(SETTINGS).effective(owner.scope.workspace_id)
    assert effective.provider_order == ["openai_compat", "anthropic"]
    assert (
        effective.anthropic.high.model == "claude-opus-5-5" and effective.anthropic.high.max_tokens == 12000
    )
    assert effective.anthropic.medium.model == harness.settings.llm.anthropic.medium.model
    compat = effective.openai_compat
    assert compat.low.timeout_s == 15 and compat.low.max_retries == 3 and compat.token_param == "max_tokens"
    assert (
        compat.extra_body == {"provider": {"sort": "price"}} and compat.api_key.get_secret_value() == SECRET
    )

    disabled = await harness.client.put(
        LLM, json={"providers": {"openai_compat": {"enabled": False}}}, headers=owner.headers
    )
    assert disabled.json()["providers"]["openai_compat"]["enabled"] is False
    effective = await harness.state.service(SETTINGS).effective(owner.scope.workspace_id)
    assert effective.provider_order == ["anthropic"] and effective.enabled

    await harness.client.put(
        LLM, json={"providers": {"anthropic": {"enabled": False}}}, headers=owner.headers
    )
    assert (await harness.state.service(SETTINGS).effective(owner.scope.workspace_id)).enabled is False

    reset = await harness.client.put(
        LLM, json={"providers": {"anthropic": {"reset": True}}}, headers=owner.headers
    )
    anthropic = reset.json()["providers"]["anthropic"]
    assert anthropic["overridden"] == [] and anthropic["enabled"]


async def test_llm_settings_reject_unsafe_or_misplaced_fields(harness: Harness) -> None:
    owner = await tenant(harness, "llmbad2")
    cases = [
        {"providers": {"anthropic": {"extra_params": {"temperature": 0.1}}}},
        {
            "providers": {
                "openai_compat": {"base_url": "https://g.example.com", "extra_params": {"model": "x"}}
            }
        },
        {"providers": {"anthropic": {"tiers": {"low": {"timeout_s": 0.5}}}}},
        {"providers": {"anthropic": {"tiers": {"low": {"effort": "max"}}}}},
    ]
    for body in cases:
        response = await harness.client.put(LLM, json=body, headers=owner.headers)
        assert response.status_code == 422, body


async def test_llm_connection_test_checks_only_the_chosen_provider(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await tenant(harness, "llmtest")
    seen = []

    async def fake_health(settings):
        seen.append((settings.anthropic.configured, settings.openai_compat.configured))
        return [RouteHealth("openai_compat", settings.openai_compat.low.model, "ok", "reachable")]

    monkeypatch.setattr(settings_service, "check_llm_health", fake_health)
    await harness.client.put(
        LLM,
        json={
            "providers": {
                "openai_compat": {
                    "base_url": "https://gateway.example.com/v1",
                    "api_key": SECRET,
                    "tiers": {"low": {"model": "small"}},
                }
            }
        },
        headers=owner.headers,
    )
    result = await harness.client.post(
        f"{LLM}/test", json={"provider": "openai_compat"}, headers=owner.headers
    )
    assert result.status_code == 200, result.text
    assert result.json()["ok"] and result.json()["checks"][0]["model"] == "small"
    assert seen == [(False, True)]


async def test_youtube_connect_says_not_configured(harness: Harness) -> None:
    owner = await tenant(harness, "oauthoff")
    response = await harness.client.get(
        "/api/v1/integrations/youtube/connect",
        params={"client_id": owner.client["id"]},
        headers=owner.headers,
    )
    assert response.status_code == 503
    assert response.json()["code"] == "integration_not_configured"
    assert "GOOGLE_OAUTH_CLIENT_ID" in response.json()["detail"]


class FakeOAuth:
    def __init__(self) -> None:
        self.completed: list[tuple[str, str]] = []
        self.disconnected = 0

    async def start(self, scope, user_id) -> str:
        return "https://accounts.google.com/o/oauth2/v2/auth?state=tok123&client_id=x"

    async def complete(self, state: str, code: str, *, user_id) -> OAuthOutcome:
        self.completed.append((state, code))
        return OAuthOutcome(status="connected")

    async def disconnect(self, scope) -> bool:
        self.disconnected += 1
        return True


async def sign_in(harness: Harness, owner) -> None:
    # google returns the browser to the callback, so it must carry the session that started the flow
    login = await harness.client.post(
        "/api/v1/auth/login", json={"email": owner.account.email, "password": PASSWORD}
    )
    assert login.status_code == 200, login.text


async def test_youtube_oauth_round_trip_redirects_to_the_client(harness: Harness) -> None:
    owner = await tenant(harness, "oauthon")
    fake = FakeOAuth()
    harness.state.override_service(YOUTUBE_OAUTH, fake)
    await sign_in(harness, owner)
    connect = await harness.client.get(
        "/api/v1/integrations/youtube/connect", params={"client_id": owner.client["id"]}
    )
    assert connect.status_code == 200, connect.text
    assert parse_qs(urlparse(connect.json()["authorization_url"]).query)["state"] == ["tok123"]

    callback = await harness.client.get(
        "/api/v1/integrations/youtube/callback",
        params={"state": "tok123", "code": "abc"},
        follow_redirects=False,
    )
    assert callback.status_code == 302
    assert callback.headers["location"].endswith(
        f"/c/{owner.client['id']}/settings?tab=integrations&oauth=connected"
    )
    assert fake.completed == [("tok123", "abc")]
    replay = await harness.client.get(
        "/api/v1/integrations/youtube/callback",
        params={"state": "tok123", "code": "abc"},
        follow_redirects=False,
    )
    assert "reason=invalid_state" in replay.headers["location"] and len(fake.completed) == 1

    disconnect = await harness.client.delete(f"{owner.path}/integrations/youtube", headers=owner.headers)
    assert disconnect.status_code == 204 and fake.disconnected == 1


async def test_youtube_callback_reports_a_declined_consent(harness: Harness) -> None:
    owner = await tenant(harness, "oauthno")
    harness.state.override_service(YOUTUBE_OAUTH, FakeOAuth())
    await sign_in(harness, owner)
    await harness.client.get("/api/v1/integrations/youtube/connect", params={"client_id": owner.client["id"]})
    declined = await harness.client.get(
        "/api/v1/integrations/youtube/callback",
        params={"state": "tok123", "error": "access_denied"},
        follow_redirects=False,
    )
    assert "reason=access_denied" in declined.headers["location"]


async def test_evaluation_routes_reach_12s_services(harness: Harness) -> None:
    owner = await tenant(harness, "evals")
    headers = owner.headers
    labels = await harness.client.get(
        "/api/v1/evaluation/outlier-labels", params={"client_id": owner.client["id"]}, headers=headers
    )
    assert labels.status_code == 200, labels.text
    assert labels.json()["items"] == []
    precision = (await harness.client.get("/api/v1/evaluation/outlier-precision", headers=headers)).json()
    assert precision["n"] == 0 and precision["threshold"] > 0
    queue = (await harness.client.get("/api/v1/evaluation/reviews/queue", headers=headers)).json()
    assert queue["items"] == []
    summary = (await harness.client.get("/api/v1/evaluation/reviews/summary", headers=headers)).json()
    assert summary["n_items"] == 0
    review = await harness.client.post(
        "/api/v1/evaluation/reviews",
        json={
            "recommendation_id": "rec_aaaaaaaaaaaa",
            "reviewer": "owner",
            "specific": 4,
            "evidenced": 4,
            "relevant": 4,
            "actionable": 4,
            "not_obvious": 3,
            "would_ship": True,
        },
        headers=headers,
    )
    assert review.status_code == 404
    label = await harness.client.put(
        "/api/v1/evaluation/outlier-labels/abcdefghijk",
        json={"is_outlier": True, "labeller": "owner"},
        headers=headers,
    )
    assert label.status_code == 404
