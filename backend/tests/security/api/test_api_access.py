import uuid

import pytest
from sqlalchemy import delete, select, update

from app.api import ids
from app.models.tenancy import ApiKey, Workspace, WorkspaceMember
from tests.api.conftest import PASSWORD, Harness, HarnessFactory
from tests.api_services.conftest import tenant

pytestmark = pytest.mark.db


def run_headers(headers: dict[str, str]) -> dict[str, str]:
    return {**headers, "Idempotency-Key": uuid.uuid4().hex}


async def test_another_workspace_cannot_drain_a_clients_run_bucket(make_harness: HarnessFactory) -> None:
    h = await make_harness(api={"rate_limits": {"run_start_per_hour_client": 2}})
    victim = await tenant(h, "rlA")
    attacker = await tenant(h, "rlB")
    for _ in range(3):
        probe = await h.client.post(
            f"{victim.path}/runs", json={"kind": "analysis"}, headers=run_headers(attacker.headers)
        )
        assert probe.status_code == 404
    own = await h.client.post(
        f"{victim.path}/runs", json={"kind": "analysis"}, headers=run_headers(victim.headers)
    )
    assert own.status_code in {200, 202}, own.text


async def test_one_idempotency_key_cannot_replay_across_clients(harness: Harness) -> None:
    first = await tenant(harness, "idem")
    second = await harness.create_client(first.account, name="Second Clinic")
    headers = {**first.headers, "Idempotency-Key": "shared-key-1"}
    body = {"kind": "analysis"}
    created = await harness.client.post(f"{first.path}/runs", json=body, headers=headers)
    assert created.status_code in {200, 202}, created.text
    reused = await harness.client.post(f"/api/v1/clients/{second['id']}/runs", json=body, headers=headers)
    assert reused.status_code == 422 and reused.json()["code"] == "idempotency_key_reused"


async def _key_row(harness: Harness, workspace_id: str) -> ApiKey:
    async with harness.container.session_factory() as session:
        return (
            await session.execute(
                select(ApiKey).where(ApiKey.workspace_id == ids.decode(ids.WORKSPACE, workspace_id))
            )
        ).scalar_one()


async def _set_scopes(harness: Harness, workspace_id: str, scopes: list[str]) -> None:
    key = await _key_row(harness, workspace_id)
    async with harness.container.session_factory() as session, session.begin():
        await session.execute(update(ApiKey).where(ApiKey.id == key.id).values(scopes=scopes))


async def test_api_key_scopes_are_enforced_per_request(harness: Harness) -> None:
    owner = await tenant(harness, "scopes")
    workspace = owner.account.workspace_id
    await _set_scopes(harness, workspace, ["read"])
    assert (await harness.client.get("/api/v1/clients", headers=owner.headers)).status_code == 200
    write = await harness.client.patch(owner.path, json={"name": "Renamed"}, headers=owner.headers)
    assert write.status_code == 403

    await _set_scopes(harness, workspace, ["runs:write"])
    assert (await harness.client.get("/api/v1/clients", headers=owner.headers)).status_code == 200
    rename = await harness.client.patch(owner.path, json={"name": "Renamed"}, headers=owner.headers)
    assert rename.status_code == 403 and "clients:write" in rename.json()["detail"]
    run = await harness.client.post(
        f"{owner.path}/runs", json={"kind": "analysis"}, headers=run_headers(owner.headers)
    )
    assert run.status_code in {200, 202}, run.text

    await _set_scopes(harness, workspace, [])
    assert (await harness.client.get("/api/v1/clients", headers=owner.headers)).status_code == 403


async def test_keys_die_with_their_creators_membership(harness: Harness) -> None:
    owner = await tenant(harness, "leaver")
    key = await _key_row(harness, owner.account.workspace_id)
    async with harness.container.session_factory() as session, session.begin():
        await session.execute(
            delete(WorkspaceMember).where(WorkspaceMember.user_id == key.created_by_user_id)
        )
    refused = await harness.client.get("/api/v1/clients", headers=owner.headers)
    assert refused.status_code == 401
    assert (await _key_row(harness, owner.account.workspace_id)).revoked_at is not None
    async with harness.container.session_factory() as session, session.begin():
        session.add(
            WorkspaceMember(workspace_id=key.workspace_id, user_id=key.created_by_user_id, role="owner")
        )
    assert (await harness.client.get("/api/v1/clients", headers=owner.headers)).status_code == 401


async def test_keys_stop_working_when_the_workspace_is_deleted(harness: Harness) -> None:
    owner = await tenant(harness, "gone")
    async with harness.container.session_factory() as session, session.begin():
        await session.execute(
            update(Workspace)
            .where(Workspace.id == ids.decode(ids.WORKSPACE, owner.account.workspace_id))
            .values(deleted_at=harness.container.clock.now())
        )
    assert (await harness.client.get("/api/v1/clients", headers=owner.headers)).status_code == 401


async def test_login_attempts_are_limited_per_account(make_harness: HarnessFactory) -> None:
    h = await make_harness(api={"rate_limits": {"login_per_hour_account": 2}})
    account = await h.account("brute")
    for _ in range(2):
        wrong = await h.client.post(
            "/api/v1/auth/login", json={"email": account.email, "password": "nope-nope"}
        )
        assert wrong.status_code == 401
    blocked = await h.client.post("/api/v1/auth/login", json={"email": account.email, "password": PASSWORD})
    assert blocked.status_code == 429
    other = await h.account("bystander")
    fine = await h.client.post("/api/v1/auth/login", json={"email": other.email, "password": PASSWORD})
    assert fine.status_code == 200
