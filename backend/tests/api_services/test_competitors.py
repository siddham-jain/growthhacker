import pytest

from app.core.config import CompetitorSettings
from app.services.competitors.repository import CompetitorRepository, SqlBrandChannelCache
from app.services.competitors.runner import DiscoveryRequest, DiscoveryRunner
from app.services.competitors.service import DiscoveryDeps
from tests.api.conftest import Harness
from tests.api_services.conftest import Tenant, seed_profile, tenant
from tests.competitors.apollo import (
    EXPECTED_COMPETITORS,
    MAX,
    MAYO,
    apollo_crawler,
    apollo_llm,
    apollo_profile,
    apollo_web_search,
    apollo_youtube,
)
from tests.competitors.fakes import MemoryLocks

pytestmark = pytest.mark.db


def fake_ports(harness: Harness) -> None:
    for port, fake in (
        ("youtube", apollo_youtube()),
        ("web_search", apollo_web_search()),
        ("site_crawler", apollo_crawler()),
        ("llm", apollo_llm()),
    ):
        harness.container.override(port, fake)


async def discovered(harness: Harness, label: str) -> Tenant:
    owner = await tenant(harness, label)
    await seed_profile(harness, owner.scope, apollo_profile())
    deps = DiscoveryDeps(
        youtube=apollo_youtube(),
        web_search=apollo_web_search(),
        crawler=apollo_crawler(),
        llm=apollo_llm(),
        cache=SqlBrandChannelCache(harness.container.session_factory),
    )
    runner = DiscoveryRunner(
        harness.container.session_factory, MemoryLocks(), deps, CompetitorSettings(), harness.container.clock
    )
    await runner.run(DiscoveryRequest(scope=owner.scope, trigger="onboarding", profile=apollo_profile()))
    return owner


async def frozen(harness: Harness, owner: Tenant) -> list[str]:
    async with harness.container.session_factory() as session:
        latest = await CompetitorRepository(session, owner.scope).active_set()
    return sorted(latest.competitor_channel_ids) if latest else []


def by_channel(items: list[dict]) -> dict[str, dict]:
    return {item["channel"]["channel_id"]: item for item in items if item["channel"]}


async def test_competitor_set_edits_freeze_after_every_decision(harness: Harness) -> None:
    owner = await discovered(harness, "rivals")
    listed = await harness.client.get(
        f"{owner.path}/competitors", params={"limit": 100}, headers=owner.headers
    )
    assert listed.status_code == 200, listed.text
    items = by_channel(listed.json()["items"])
    assert set(items) >= EXPECTED_COMPETITORS
    sheet = items[MAX]
    assert "score" not in sheet and sheet["business"]["tier"] in {"high", "medium", "low"}
    assert sheet["channels"][0]["ownership"]["label"] and sheet["evidence"]

    for channel_id in sorted(EXPECTED_COMPETITORS - {MAX}):
        response = await harness.client.post(
            f"{owner.path}/competitors/{items[channel_id]['id']}/confirm", headers=owner.headers
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "confirmed" and response.json()["decided_by"] == "user"
    assert await frozen(harness, owner) == sorted(EXPECTED_COMPETITORS - {MAX})

    rejected = await harness.client.post(
        f"{owner.path}/competitors/{items[MAX]['id']}/reject",
        json={"reason": "too_big"},
        headers=owner.headers,
    )
    assert rejected.json()["status"] == "rejected" and rejected.json()["reject_reason"] == "too_big"

    moved = await harness.client.patch(
        f"{owner.path}/competitors/{items[MAYO]['id']}",
        json={"role": "niche_set"},
        headers=owner.headers,
    )
    assert moved.status_code == 200 and moved.json()["role"] == "niche_set"

    keep = sorted(EXPECTED_COMPETITORS - {MAX})
    for channel_id in keep[:2]:
        removed = await harness.client.delete(
            f"{owner.path}/competitors/{items[channel_id]['id']}", headers=owner.headers
        )
        assert removed.status_code == 204
    # two competitors left: the decisions are saved, the last valid set stays until the selection is valid
    assert await frozen(harness, owner) == keep[1:]
    filtered = await harness.client.get(
        f"{owner.path}/competitors", params={"status": "rejected"}, headers=owner.headers
    )
    assert [item["channel"]["channel_id"] for item in filtered.json()["items"]] == [MAX]
    assert (
        await harness.client.get(
            f"{owner.path}/competitors", params={"status": "active"}, headers=owner.headers
        )
    ).status_code == 422


async def test_adding_a_competitor_resolves_the_channel_and_marks_it_user_added(harness: Harness) -> None:
    owner = await discovered(harness, "addrival")
    fake_ports(harness)
    added = await harness.client.post(
        f"{owner.path}/competitors",
        json={"channel_ref": f"https://www.youtube.com/channel/{MAX}", "role": "niche_set"},
        headers=owner.headers,
    )
    assert added.status_code == 201, added.text
    body = added.json()
    assert body["status"] == "user_added" and body["role"] == "niche_set" and body["added_by"] == "user"
    assert body["channel"]["channel_id"] == MAX

    unknown = await harness.client.post(
        f"{owner.path}/competitors", json={"channel_ref": "@definitely-not-a-channel"}, headers=owner.headers
    )
    assert unknown.status_code == 422 and unknown.json()["code"] == "channel_unresolvable"


async def test_adding_needs_a_profile_first(harness: Harness) -> None:
    owner = await tenant(harness, "noprofile")
    fake_ports(harness)
    response = await harness.client.post(
        f"{owner.path}/competitors",
        json={"channel_ref": f"https://www.youtube.com/channel/{MAX}"},
        headers=owner.headers,
    )
    assert response.status_code == 422 and response.json()["code"] == "config_invalid"
