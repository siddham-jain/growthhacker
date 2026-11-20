import asyncio

import pytest
from sqlalchemy import func, select

from app.domain.profile import ClaimStatus, FunnelStage, Location, LocationSummary, ReviewStatus
from app.models.tenancy import Client
from app.models.topics import TopicRef as TopicRefRow
from app.models.topics import TopicUniverse
from app.models.website import Keyword as KeywordRow
from app.services.website_intel.persistence import WebsiteIntelStore
from app.services.website_intel.profile.merger import ProfileMerger
from app.services.website_intel.questions.service import normalise_question
from tests.api.conftest import Harness
from tests.api_services.conftest import Tenant, seed_profile, tenant
from tests.topics.fixtures import keyword, meta, offering, profile, question

pytestmark = pytest.mark.db

MERGE_PATCH = {"Content-Type": "application/merge-patch+json"}


async def seeded(harness: Harness, label: str) -> Tenant:
    owner = await tenant(harness, label)
    machine = profile(
        owner.scope.client_id,
        name="Apollo Hospitals",
        offerings=[
            offering("off_knee", "Knee replacement"),
            offering("off_heart", "Angioplasty", status=ClaimStatus.proposed),
        ],
        keywords=[
            keyword("kw_knee", "knee replacement"),
            keyword("kw_spine", "spine surgery", status=ClaimStatus.proposed),
        ],
    )
    await seed_profile(harness, owner.scope, machine)
    async with harness.container.session_factory() as session, session.begin():
        await WebsiteIntelStore(session, owner.scope).save_questions(
            [
                question(owner.scope.client_id, "What does knee replacement cost?"),
                question(owner.scope.client_id, "Is angioplasty painful?", status=ReviewStatus.proposed),
            ]
        )
    return owner


async def refs(harness: Harness, owner: Tenant) -> set[str]:
    async with harness.container.session_factory() as session:
        version = (
            await session.execute(
                select(func.max(TopicUniverse.profile_version)).where(
                    TopicUniverse.client_id == owner.scope.client_id
                )
            )
        ).scalar_one()
        rows = await session.execute(
            select(TopicRefRow.label).where(
                TopicRefRow.client_id == owner.scope.client_id, TopicRefRow.profile_version == version
            )
        )
        return set(rows.scalars())


async def test_profile_edits_override_and_rejections_survive_a_recrawl(harness: Harness) -> None:
    owner = await seeded(harness, "profile")
    view = await harness.client.get(f"{owner.path}/profile", headers=owner.headers)
    assert view.status_code == 200, view.text
    assert view.headers["etag"] == '"1"' and view.json()["overrides"] == {}

    stale = await harness.client.patch(
        f"{owner.path}/profile",
        json={"offerings": {"off_heart": None}},
        headers={**owner.headers, **MERGE_PATCH, "If-Match": '"7"'},
    )
    assert stale.status_code == 412
    patch = {
        "identity": {"one_liner": "Hospitals across India"},
        "offerings": {"off_heart": None, "off_knee": {"status": "confirmed"}},
        "niche_keywords": {
            "kw_cardiac": {
                "term": "cardiac surgery",
                "language": "en",
                "kind": "procedure",
                "tier": "seed",
                "source": "user",
                "weight": 0.9,
            }
        },
    }
    edited = await harness.client.patch(
        f"{owner.path}/profile", json=patch, headers={**owner.headers, **MERGE_PATCH, "If-Match": '"1"'}
    )
    assert edited.status_code == 200, edited.text
    body = edited.json()
    assert body["version"] == 2 and edited.headers["etag"] == '"2"'
    assert body["overrides"]["offerings"]["off_heart"] is None
    effective = body["effective"]
    assert [item["id"] for item in effective["offerings"]] == ["off_knee"]
    assert effective["offerings"][0]["meta"]["locked"] is True
    assert effective["identity"]["one_liner"] == "Hospitals across India"
    assert body["profile"]["identity"]["one_liner"] == "Apollo Hospitals profile"
    assert {"Knee replacement", "cardiac surgery"} <= await refs(harness, owner)

    async with harness.container.session_factory() as session:
        store = WebsiteIntelStore(session, owner.scope)
        current, stones = await store.latest_profile(), await store.tombstones()
    recrawled = profile(
        owner.scope.client_id,
        name="Apollo Hospitals",
        offerings=[offering("off_heart", "Angioplasty")],
        version=3,
    )
    merged = ProfileMerger().merge(current, recrawled, stones)
    assert "off_heart" not in {item.id for item in merged.offerings}

    unknown = await harness.client.patch(
        f"{owner.path}/profile",
        json={"freshness": {}},
        headers={**owner.headers, **MERGE_PATCH, "If-Match": '"2"'},
    )
    assert unknown.status_code == 422 and unknown.json()["code"] == "validation_failed"


async def test_onboarding_corrections_cover_locations_contacts_country_and_undo(harness: Harness) -> None:
    owner = await tenant(harness, "corrections")
    machine = profile(
        owner.scope.client_id, name="LeRoy", offerings=[offering("off_box", "Corrugated boxes")]
    )
    locations = [
        Location(id="loc_hq", name="Hoshiarpur office", kind="office", city="Hoshiarpur", meta=meta()),
        Location(id="loc_bienne", name="Bienne, Switzerland", kind="branch", city="Bienne", meta=meta()),
    ]
    machine = machine.model_copy(
        update={"locations": LocationSummary(count=2, cities=["Hoshiarpur", "Bienne"], items=locations)}
    )
    await seed_profile(harness, owner.scope, machine)

    async def patch(body: dict, version: int) -> dict:
        response = await harness.client.patch(
            f"{owner.path}/profile",
            json=body,
            headers={**owner.headers, **MERGE_PATCH, "If-Match": f'"{version}"'},
        )
        assert response.status_code == 200, response.text
        return response.json()

    body = await patch(
        {
            "locations": {
                "loc_bienne": None,
                "loc_hq": {"name": "Head office, Hoshiarpur"},
                "user_ludhiana": {"name": "Ludhiana depot", "city": "Ludhiana"},
            },
            "conversion_actions": {"user_call": {"label": "+91 97809 00339"}},
            "offerings": {"user_cartons": {"name": "Folding cartons"}},
            "identity": {"country": "in"},
        },
        1,
    )
    effective = body["effective"]
    summary = effective["locations"]
    assert [(item["id"], item["name"]) for item in summary["items"]] == [
        ("loc_hq", "Head office, Hoshiarpur"),
        ("user_ludhiana", "Ludhiana depot"),
    ]
    assert summary["cities"] == ["Hoshiarpur", "Ludhiana"] and summary["count"] == 2
    assert summary["items"][0]["meta"]["locked"] is True
    call = effective["conversion_actions"][0]
    assert (call["type"], call["medium"], call["target"]) == ("call", "phone", "tel:+919780900339")
    assert effective["offerings"][1] == {
        **effective["offerings"][1],
        "name": "Folding cartons",
        "kind": "other",
    }
    assert effective["identity"]["country"] == "IN"
    async with harness.container.session_factory() as session:
        client = await session.get(Client, owner.scope.client_id)
        assert client is not None and client.country == "IN" and client.timezone == "Asia/Kolkata"
        assert client.settings["market_explicit"] is True
        stones = await WebsiteIntelStore(session, owner.scope).tombstones()
        current = await WebsiteIntelStore(session, owner.scope).latest_profile()
    assert [(stone.collection, stone.item_key) for stone in stones] == [("locations", "loc_bienne")]

    recrawled = machine.model_copy(update={"version": 3})
    merged = ProfileMerger().merge(current, recrawled, stones)
    assert [item.name for item in merged.locations.items] == ["Head office, Hoshiarpur", "Ludhiana depot"]

    restored = await patch(
        {"locations": {"loc_bienne": {"name": "Bienne, Switzerland", "kind": "branch"}}}, 2
    )
    assert "loc_bienne" in {item["id"] for item in restored["effective"]["locations"]["items"]}
    async with harness.container.session_factory() as session:
        assert await WebsiteIntelStore(session, owner.scope).tombstones() == []

    bad = await harness.client.patch(
        f"{owner.path}/profile",
        json={"identity": {"country": "India"}},
        headers={**owner.headers, **MERGE_PATCH, "If-Match": '"3"'},
    )
    assert bad.status_code == 422


async def test_keywords_use_review_statuses_and_rejected_ones_stay_rejected(harness: Harness) -> None:
    owner = await seeded(harness, "keywords")
    listed = (await harness.client.get(f"{owner.path}/keywords", headers=owner.headers)).json()
    assert [(item["id"], item["status"]) for item in listed["items"]] == [
        ("kw_knee", "confirmed"),
        ("kw_spine", "proposed"),
    ]

    added = await harness.client.post(
        f"{owner.path}/keywords", json={"text": "heart bypass"}, headers=owner.headers
    )
    assert added.status_code == 201, added.text
    assert added.json()["status"] == "confirmed" and added.json()["source"] == "user"
    duplicate = await harness.client.post(
        f"{owner.path}/keywords", json={"text": "Heart Bypass"}, headers=owner.headers
    )
    assert duplicate.status_code == 409

    bulk = await harness.client.post(
        f"{owner.path}/keywords/bulk-status",
        json={"ids": ["kw_spine"], "status": "rejected"},
        headers=owner.headers,
    )
    assert bulk.json() == {"updated": 1}
    rejected = (
        await harness.client.get(
            f"{owner.path}/keywords", params={"status": "rejected"}, headers=owner.headers
        )
    ).json()
    assert [(item["id"], item["text"]) for item in rejected["items"]] == [("kw_spine", "spine surgery")]
    assert "spine surgery" not in await refs(harness, owner)
    assert "heart bypass" in await refs(harness, owner)

    weighted = await harness.client.patch(
        f"{owner.path}/keywords/kw_knee", json={"weight": 0.4}, headers=owner.headers
    )
    assert weighted.json()["weight"] == 0.4
    wrong_field = await harness.client.patch(
        f"{owner.path}/keywords/kw_knee", json={"priority": 1}, headers=owner.headers
    )
    assert wrong_field.status_code == 422
    restored = await harness.client.patch(
        f"{owner.path}/keywords/kw_spine", json={"status": "confirmed"}, headers=owner.headers
    )
    assert restored.status_code == 200 and restored.json()["status"] == "confirmed"
    async with harness.container.session_factory() as session:
        mirrored = dict(
            (
                await session.execute(
                    select(KeywordRow.text, KeywordRow.status).where(
                        KeywordRow.client_id == owner.scope.client_id
                    )
                )
            ).all()
        )
    assert mirrored["spine surgery"] == "confirmed" and mirrored["heart bypass"] == "confirmed"

    old = await harness.client.get(
        f"{owner.path}/keywords", params={"status": "active"}, headers=owner.headers
    )
    assert old.status_code == 422

    # corrections arrive in bursts; each writes the next profile version, so they queue instead of colliding
    burst = await asyncio.gather(
        *(
            harness.client.post(f"{owner.path}/keywords", json={"text": text}, headers=owner.headers)
            for text in ("folding cartons", "mono cartons", "rigid boxes")
        ),
        harness.client.delete(f"{owner.path}/keywords/kw_knee", headers=owner.headers),
    )
    assert [response.status_code for response in burst] == [201, 201, 201, 204]


async def test_buyer_questions_create_edit_and_delete_keep_user_decisions(harness: Harness) -> None:
    owner = await seeded(harness, "questions")
    listed = (await harness.client.get(f"{owner.path}/buyer-questions", headers=owner.headers)).json()[
        "items"
    ]
    assert [item["status"] for item in listed] == ["confirmed", "proposed"]
    proposed = listed[1]["id"]

    created = await harness.client.post(
        f"{owner.path}/buyer-questions",
        json={"text": "How long is knee surgery recovery?", "priority": 1},
        headers=owner.headers,
    )
    assert created.status_code == 201, created.text
    question_body = created.json()
    assert question_body["source"] == "user" and question_body["funnel_stage"] in {
        stage.value for stage in FunnelStage
    }
    again = await harness.client.post(
        f"{owner.path}/buyer-questions",
        json={"text": "How long is knee surgery recovery?"},
        headers=owner.headers,
    )
    assert again.status_code == 409

    edited = await harness.client.patch(
        f"{owner.path}/buyer-questions/{proposed}",
        json={"status": "confirmed", "priority": 3},
        headers=owner.headers,
    )
    assert edited.json()["status"] == "confirmed" and edited.json()["priority"] == 3
    assert "Is angioplasty painful?" in await refs(harness, owner)

    deleted = await harness.client.delete(
        f"{owner.path}/buyer-questions/{question_body['id']}", headers=owner.headers
    )
    assert deleted.status_code == 204
    async with harness.container.session_factory() as session, session.begin():
        recrawled = question(owner.scope.client_id, "How long is knee surgery recovery?")
        normalized = normalise_question(recrawled.text, [])[0]
        await WebsiteIntelStore(session, owner.scope).save_questions(
            [recrawled.model_copy(update={"normalized_text": normalized})]
        )
    after = (await harness.client.get(f"{owner.path}/buyer-questions", headers=owner.headers)).json()
    recovery = [
        item["status"] for item in after["items"] if item["text"] == "How long is knee surgery recovery?"
    ]
    assert recovery == ["rejected"]
    missing = await harness.client.patch(
        f"{owner.path}/buyer-questions/bq_{'0' * 32}", json={"priority": 1}, headers=owner.headers
    )
    assert missing.status_code == 404
