import json

import pytest

from app.api.services import SETTINGS
from app.domain.youtube import (
    ChannelRecord,
    ChannelResolution,
    OwnershipLabel,
    OwnershipSignal,
    OwnershipVerdict,
)
from app.models.tenancy import Client
from app.services.youtube.input_parser import InputParser
from tests.api.conftest import Harness
from tests.api_services.conftest import tenant

pytestmark = pytest.mark.db

SECRET = "sk-test-not-a-real-key-1234"
CHANNEL = "UCx9KpQ2vR7mN4tL8wZ1aB3c"


async def test_llm_keys_are_write_only_and_override_the_environment(harness: Harness) -> None:
    owner = await tenant(harness, "llm")
    path = "/api/v1/settings/llm"
    before = (await harness.client.get(path, headers=owner.headers)).json()
    assert before["providers"]["anthropic"]["api_key_set"] is False
    assert before["providers"]["anthropic"]["tiers"]["low"]["model"] == "claude-haiku-4-5"

    body = {
        "provider_order": ["openai_compat", "anthropic"],
        "providers": {
            "openai_compat": {
                "base_url": "https://gateway.example.com/v1",
                "api_key": SECRET,
                "tiers": {"low": {"model": "small-model"}, "medium": {"model": "mid-model"}},
            }
        },
    }
    saved = await harness.client.put(path, json=body, headers=owner.headers)
    assert saved.status_code == 200, saved.text
    assert SECRET not in saved.text
    fallback = saved.json()["providers"]["openai_compat"]
    assert fallback["api_key_set"] and fallback["api_key_source"] == "workspace" and fallback["configured"]
    assert [fallback["tiers"][tier]["model"] for tier in ("low", "medium", "high")] == [
        "small-model",
        "mid-model",
        None,
    ]
    assert SECRET not in (await harness.client.get(path, headers=owner.headers)).text

    effective = await harness.state.service(SETTINGS).effective(owner.scope.workspace_id)
    assert effective.provider_order == ["openai_compat", "anthropic"]
    assert effective.openai_compat.api_key.get_secret_value() == SECRET
    assert effective.openai_compat.low.model == "small-model" and effective.openai_compat.configured

    cleared = await harness.client.put(
        path, json={"providers": {"openai_compat": {"clear_api_key": True}}}, headers=owner.headers
    )
    assert cleared.json()["providers"]["openai_compat"]["api_key_set"] is False


async def test_llm_settings_validate_urls_and_key_requests(harness: Harness) -> None:
    owner = await tenant(harness, "llmbad")
    path = "/api/v1/settings/llm"
    plain_http = {
        "providers": {"openai_compat": {"base_url": "http://gateway.example.com", "api_key": SECRET}}
    }
    assert (await harness.client.put(path, json=plain_http, headers=owner.headers)).status_code == 422
    no_url = {"providers": {"openai_compat": {"api_key": SECRET}}}
    response = await harness.client.put(path, json=no_url, headers=owner.headers)
    assert response.status_code == 422 and response.json()["errors"][0]["loc"][-1] == "base_url"
    both = {"providers": {"anthropic": {"api_key": SECRET, "clear_api_key": True}}}
    assert (await harness.client.put(path, json=both, headers=owner.headers)).status_code == 422


class FakeResolver:
    def __init__(self, resolution: ChannelResolution) -> None:
        self.resolution = resolution
        self.calls: list[tuple] = []

    async def resolve(self, raw, ctx, *, hint=None, search=None):
        self.calls.append((raw, hint, search.limit if search else None))
        return self.resolution


class FakeDataLayer:
    def __init__(self, resolver: FakeResolver) -> None:
        self.resolver = resolver


def channel(channel_id: str, title: str) -> ChannelRecord:
    return ChannelRecord.model_validate(
        {
            "channel_id": channel_id,
            "title": title,
            "handle": "@apollohospitals",
            "description": "",
            "country": "IN",
            "default_language": None,
            "published_at": "2012-01-01T00:00:00Z",
            "uploads_playlist_id": None,
            "subscriber_count": 1000,
            "hidden_subscriber_count": False,
            "view_count": 10,
            "video_count": 5,
            "thumbnail_url": None,
            "metadata_fetched_at": "2026-11-20T00:00:00Z",
            "stats_fetched_at": "2026-11-20T00:00:00Z",
        }
    )


async def test_channel_resolve_reports_ownership_and_attach_verifies_the_pick(harness: Harness) -> None:
    owner = await tenant(harness, "chan")
    verdict = OwnershipVerdict(
        label=OwnershipLabel.verified,
        method="link",
        score=0.9,
        signals=[OwnershipSignal(name="site_link", weight=0.6, detail="Linked from the site footer")],
    )
    resolver = FakeResolver(
        ChannelResolution(
            input=InputParser().parse("@apollohospitals"),
            channel=channel(CHANNEL, "Apollo Hospitals"),
            method="handle",
            candidates=[channel("UCa1b2c3d4e5f6g7h8i9j0kL", "Apollo 24|7")],
            ownership=verdict,
            needs_user_confirmation=False,
        )
    )
    harness.container.override("youtube_data_layer", FakeDataLayer(resolver))
    response = await harness.client.post(
        f"{owner.path}/channel/resolve", json={"channel_ref": "@apollohospitals"}, headers=owner.headers
    )
    assert response.status_code == 200, response.text
    candidates = response.json()["candidates"]
    assert [item["confidence"] for item in candidates] == ["high", "low"]
    assert candidates[0]["ownership_evidence"] == ["Linked from the site footer"]
    assert resolver.calls[0][2] == 1 and resolver.calls[0][1].website_domain

    attached = await harness.client.post(
        f"{owner.path}/channel/attach", json={"channel_id": CHANNEL}, headers=owner.headers
    )
    assert attached.status_code == 200, attached.text
    own = attached.json()["own_channel"]
    assert own["channel_id"] == CHANNEL and own["verified"] and own["verified_by"] == "manual"
    detached = await harness.client.post(
        f"{owner.path}/channel/attach", json={"channel_id": None}, headers=owner.headers
    )
    assert detached.json()["own_channel"] is None
    assert json.loads(detached.headers["etag"]) > json.loads(attached.headers["etag"])


async def test_channel_resolve_shows_onboarding_evidence_for_the_proposed_channel(harness: Harness) -> None:
    owner = await tenant(harness, "chanlikely")
    async with harness.container.session_factory() as session, session.begin():
        client = await session.get(Client, owner.scope.client_id)
        client.own_channel_id, client.own_channel_status = CHANNEL, "proposed"
        client.own_channel_verification = [
            {
                "verified_by": None,
                "label": "likely",
                "score": 0.5,
                "signals": [
                    {
                        "name": "site_links_channel",
                        "weight": 0.5,
                        "detail": "official site links to https://www.youtube.com/user/apollo",
                        "source_url": "https://www.youtube.com/user/apollo",
                    }
                ],
            }
        ]
    fresh = OwnershipVerdict(label=OwnershipLabel.rejected, method=None, score=0.0, signals=[])
    resolver = FakeResolver(
        ChannelResolution(
            input=InputParser().parse(CHANNEL),
            channel=channel(CHANNEL, "Apollo Hospitals"),
            method="id",
            ownership=fresh,
            needs_user_confirmation=True,
            message="doesn't link to apollohospitals.com",
        )
    )
    harness.container.override("youtube_data_layer", FakeDataLayer(resolver))
    response = await harness.client.post(
        f"{owner.path}/channel/resolve", json={"channel_ref": CHANNEL}, headers=owner.headers
    )
    assert response.status_code == 200, response.text
    [only] = response.json()["candidates"]
    assert only["confidence"] == "medium"
    assert only["ownership_evidence"] == ["official site links to https://www.youtube.com/user/apollo"]
