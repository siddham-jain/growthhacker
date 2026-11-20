import uuid

import pytest
from sqlalchemy import update

from app.api import ids
from app.models.tenancy import Client
from tests.api.conftest import Harness

pytestmark = pytest.mark.db


async def _awaiting_client(harness: Harness, account) -> str:
    client = await harness.create_client(account)
    async with harness.container.session_factory() as session, session.begin():
        await session.execute(
            update(Client)
            .where(Client.id == ids.decode(ids.CLIENT, client["id"]))
            .values(lifecycle="awaiting_confirmation")
        )
    return client["id"]


async def test_confirm_setup_answers_202_with_a_run_and_replays_idempotently(harness: Harness) -> None:
    account = await harness.account("confirm")
    client_id = await _awaiting_client(harness, account)
    path = f"/api/v1/clients/{client_id}/confirm-setup"
    key = {"Idempotency-Key": uuid.uuid4().hex}

    first = await harness.client.post(path, json={}, headers={**account.headers, **key})
    assert first.status_code == 202, first.text
    run = first.json()["run"]
    assert run["status"] == "queued" and run["kind"] == "analysis"
    assert first.headers["location"] == f"/api/v1/runs/{run['id']}"
    assert set(first.json()["validation"]) == {
        "competitors_confirmed",
        "keywords_active",
        "buyer_questions_active",
    }

    replay = await harness.client.post(path, json={}, headers={**account.headers, **key})
    assert replay.status_code == 202 and replay.headers["idempotent-replayed"] == "true"
    assert replay.json()["run"]["id"] == run["id"]

    again = await harness.client.post(
        path, json={}, headers={**account.headers, "Idempotency-Key": uuid.uuid4().hex}
    )
    assert again.status_code == 200 and again.headers["x-run-deduplicated"] == "true"
    assert again.json()["run"]["id"] == run["id"]

    events = await harness.client.get(f"/api/v1/runs/{run['id']}/events.json", headers=account.headers)
    assert events.status_code == 200
    assert [event["type"] for event in events.json()["items"]] == ["run.queued"]


async def test_confirm_setup_fails_fast_before_onboarding_finished(harness: Harness) -> None:
    account = await harness.account("early")
    client = await harness.create_client(account)
    response = await harness.client.post(
        f"/api/v1/clients/{client['id']}/confirm-setup",
        json={},
        headers={**account.headers, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert response.status_code == 422 and response.json()["code"] == "config_invalid"
