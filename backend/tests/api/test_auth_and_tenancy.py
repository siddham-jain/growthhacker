import uuid

import pytest

from app.core.registry import Registry
from tests.api.conftest import PASSWORD, Harness, HarnessFactory

pytestmark = pytest.mark.db

PROBLEM = "application/problem+json"


async def test_health_endpoints(harness: Harness) -> None:
    assert (await harness.client.get("/healthz")).json() == {"status": "ok"}
    ready = await harness.client.get("/readyz")
    assert ready.status_code == 200, ready.text


async def test_missing_and_bad_credentials_are_problems(harness: Harness) -> None:
    missing = await harness.client.get("/api/v1/clients")
    assert missing.status_code == 401
    assert missing.headers["content-type"].startswith(PROBLEM)
    assert missing.headers["www-authenticate"] == "Bearer"
    body = missing.json()
    assert body["code"] == "invalid_credentials" and body["request_id"] == missing.headers["x-request-id"]
    assert body["type"] == "https://growthhacker.dev/problems/invalid_credentials"

    forged = await harness.client.get(
        "/api/v1/clients", headers={"Authorization": f"Bearer ghk_test_{'a' * 12}_{'b' * 43}"}
    )
    assert forged.status_code == 401 and forged.json()["code"] == "invalid_credentials"


async def test_workspaces_cannot_see_each_others_clients_or_runs(harness: Harness) -> None:
    apollo = await harness.account("apollo")
    fortis = await harness.account("fortis")
    client = await harness.create_client(apollo)
    run = await harness.client.post(
        f"/api/v1/clients/{client['id']}/runs",
        json={"kind": "analysis"},
        headers={**apollo.headers, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert run.status_code == 202, run.text
    run_id = run.json()["id"]

    assert (
        await harness.client.get(f"/api/v1/clients/{client['id']}", headers=apollo.headers)
    ).status_code == 200
    for path in (
        f"/api/v1/clients/{client['id']}",
        f"/api/v1/runs/{run_id}",
        f"/api/v1/runs/{run_id}/events.json",
    ):
        response = await harness.client.get(path, headers=fortis.headers)
        assert response.status_code == 404, path
        assert response.json()["code"] == "not_found"
    listing = await harness.client.get("/api/v1/clients", headers=fortis.headers)
    assert listing.json()["items"] == []
    cancel = await harness.client.post(f"/api/v1/runs/{run_id}/cancel", headers=fortis.headers)
    assert cancel.status_code == 404


async def test_session_login_me_csrf_and_logout(harness: Harness) -> None:
    account = await harness.account("dental")
    login = await harness.client.post(
        "/api/v1/auth/login", json={"email": account.email, "password": PASSWORD}
    )
    assert login.status_code == 200, login.text
    assert "httponly" in login.headers["set-cookie"].lower()

    me = await harness.client.get("/api/v1/me")
    assert me.status_code == 200
    body = me.json()
    assert body["principal"] == "user" and body["role"] == "owner"
    assert body["active_workspace_id"] == account.workspace_id
    assert [workspace["id"] for workspace in body["workspaces"]] == [account.workspace_id]

    payload = {
        "name": "Smile Dental",
        "website_url": "https://smile.example",
        "start_onboarding": False,
        "market": {"country": "US", "language": "en", "timezone": "America/New_York"},
    }
    blocked = await harness.client.post("/api/v1/clients", json=payload)
    assert blocked.status_code == 403
    allowed = await harness.client.post(
        "/api/v1/clients", json=payload, headers={"X-Requested-With": "fetch"}
    )
    assert allowed.status_code == 201, allowed.text

    logout = await harness.client.post("/api/v1/auth/logout", headers={"X-Requested-With": "fetch"})
    assert logout.status_code == 204
    harness.client.cookies.clear()
    assert (await harness.client.get("/api/v1/me")).status_code == 401


async def test_wrong_password_is_rejected(harness: Harness) -> None:
    account = await harness.account("auto")
    response = await harness.client.post(
        "/api/v1/auth/login", json={"email": account.email, "password": "nope-nope"}
    )
    assert response.status_code == 401
    unknown = await harness.client.post(
        "/api/v1/auth/login", json={"email": "ghost@example.com", "password": "x"}
    )
    assert unknown.status_code == 401


async def test_idempotency_key_rules(harness: Harness) -> None:
    account = await harness.account("idem")
    payload = {
        "name": "Fortis",
        "website_url": "https://fortis.example",
        "start_onboarding": False,
        "market": {"country": "IN", "language": "en", "timezone": "Asia/Kolkata"},
    }
    required = await harness.client.post("/api/v1/clients", json=payload, headers=account.headers)
    assert required.status_code == 400 and required.json()["code"] == "idempotency_key_required"

    key = {"Idempotency-Key": f'"{uuid.uuid4()}"'}
    first = await harness.client.post("/api/v1/clients", json=payload, headers={**account.headers, **key})
    assert first.status_code == 201, first.text
    replay = await harness.client.post("/api/v1/clients", json=payload, headers={**account.headers, **key})
    assert replay.status_code == 201
    assert replay.headers["idempotent-replayed"] == "true"
    assert replay.json()["client"]["id"] == first.json()["client"]["id"]
    assert replay.headers["location"] == first.headers["location"]

    reused = await harness.client.post(
        "/api/v1/clients", json={**payload, "name": "Other"}, headers={**account.headers, **key}
    )
    assert reused.status_code == 422 and reused.json()["code"] == "idempotency_key_reused"

    duplicate = await harness.client.post(
        "/api/v1/clients", json=payload, headers={**account.headers, "Idempotency-Key": uuid.uuid4().hex}
    )
    assert duplicate.status_code == 409 and duplicate.json()["code"] == "client_exists"


async def test_runs_dedupe_and_unknown_query_params(harness: Harness) -> None:
    account = await harness.account("runs")
    client = await harness.create_client(account)
    path = f"/api/v1/clients/{client['id']}/runs"
    first = await harness.client.post(
        path, json={"kind": "analysis"}, headers={**account.headers, "Idempotency-Key": uuid.uuid4().hex}
    )
    second = await harness.client.post(
        path, json={"kind": "analysis"}, headers={**account.headers, "Idempotency-Key": uuid.uuid4().hex}
    )
    assert first.status_code == 202 and second.status_code == 200
    assert second.headers["x-run-deduplicated"] == "true"
    assert first.json()["id"] == second.json()["id"]
    assert [stage["stage"] for stage in first.json()["stages"]] == ["a"]
    assert first.headers["location"] == f"/api/v1/runs/{first.json()['id']}"

    listing = await harness.client.get(path, headers=account.headers)
    assert [run["id"] for run in listing.json()["items"]] == [first.json()["id"]]
    typo = await harness.client.get(path, params={"knd": "analysis"}, headers=account.headers)
    assert typo.status_code == 400

    unavailable = await harness.client.post(
        path, json={"kind": "recrawl"}, headers={**account.headers, "Idempotency-Key": uuid.uuid4().hex}
    )
    assert unavailable.status_code == 501 and unavailable.json()["code"] == "not_implemented"


async def test_unwired_services_answer_501(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    # a deployment without the service implementations registered
    monkeypatch.setattr("app.api.state.api_service_registry", Registry("api_service_registry"))
    account = await harness.account("unwired")
    client = await harness.create_client(account)
    for path in ("profile", "competitors", "keywords", "analytics/trending", "recommendations"):
        response = await harness.client.get(f"/api/v1/clients/{client['id']}/{path}", headers=account.headers)
        assert response.status_code == 501, path
        assert response.json()["code"] == "not_implemented"


async def test_validation_errors_carry_field_errors(harness: Harness) -> None:
    account = await harness.account("invalid")
    response = await harness.client.post(
        "/api/v1/clients",
        json={"name": "", "market": {"country": "india", "language": "en", "timezone": "Mars/Base"}},
        headers={**account.headers, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "validation_failed"
    assert {tuple(error["loc"][-2:]) for error in body["errors"]} >= {
        ("market", "country"),
        ("market", "timezone"),
    }


async def test_rate_limits_return_429_with_headers(make_harness: HarnessFactory) -> None:
    harness = await make_harness(api={"rate_limits": {"read_per_min": 2}})
    account = await harness.account("limited")
    statuses = [(await harness.client.get("/api/v1/clients", headers=account.headers)) for _ in range(3)]
    assert [response.status_code for response in statuses] == [200, 200, 429]
    limited = statuses[-1]
    assert limited.json()["code"] == "rate_limited"
    assert int(limited.headers["retry-after"]) >= 1
    assert limited.headers["ratelimit-policy"] == '"read";q=2;w=60'
    assert statuses[0].headers["ratelimit"].startswith('"read";r=1')
