import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest
from sqlalchemy import update

from app.api.services import SETTINGS
from app.api.services_impl import settings as settings_service
from app.api.services_impl.settings import LLMConfigService
from app.models.tenancy import Workspace
from app.services.website_intel.inputs.ssrf import SsrfGuard
from tests.api.conftest import Harness, HarnessFactory
from tests.api_services.conftest import ENCRYPTION_KEY, tenant

pytestmark = pytest.mark.db
LLM = "/api/v1/settings/llm"
OPERATOR_KEY = "sk-OPERATOR-ENV-SECRET"
WORKSPACE_KEY = "sk-workspace-own-key-123"


@pytest.fixture
def listener() -> Iterator[tuple[int, list[dict[str, str]]]]:
    seen: list[dict[str, str]] = []

    class Capture(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:
            pass

        def do_GET(self) -> None:
            seen.append(dict(self.headers))
            self.send_response(404)
            self.end_headers()

        do_POST = do_GET

    server = HTTPServer(("127.0.0.1", 0), Capture)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_port, seen
    server.shutdown()


@pytest.fixture
async def operator(make_harness: HarnessFactory, monkeypatch: pytest.MonkeyPatch) -> Harness:
    monkeypatch.setenv(
        "GH_SECURITY__TOKEN_ENCRYPTION_KEYS", json.dumps([{"kid": "k1", "key": ENCRYPTION_KEY}])
    )
    return await make_harness(llm={"anthropic": {"api_key": OPERATOR_KEY}})


@pytest.fixture
def health_calls(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    calls: list[Any] = []

    async def record(settings: Any) -> list[Any]:
        calls.append(settings)
        return []

    monkeypatch.setattr(settings_service, "check_llm_health", record)
    return calls


async def _store_legacy(harness: Harness, workspace_id: Any, providers: dict[str, Any]) -> None:
    # settings saved before the pairing rule existed
    async with harness.container.session_factory() as session, session.begin():
        await session.execute(
            update(Workspace)
            .where(Workspace.id == workspace_id)
            .values(settings={"llm": {"providers": providers}})
        )


async def test_the_operator_key_never_travels_to_a_workspace_base_url(operator: Harness, listener) -> None:
    port, seen = listener
    owner = await tenant(operator, "llmexfil")
    local = {"providers": {"anthropic": {"base_url": f"http://127.0.0.1:{port}"}}}
    assert (await operator.client.put(LLM, json=local, headers=owner.headers)).status_code == 422
    public = {"providers": {"anthropic": {"base_url": "https://gateway.example.com"}}}
    refused = await operator.client.put(LLM, json=public, headers=owner.headers)
    assert refused.status_code == 422 and refused.json()["errors"][0]["loc"][-1] == "api_key"
    await operator.client.post(f"{LLM}/test", json={"provider": "anthropic"}, headers=owner.headers)
    assert all(OPERATOR_KEY not in json.dumps(headers) for headers in seen)


async def test_stored_custom_base_urls_never_borrow_the_operator_key(operator: Harness, health_calls) -> None:
    owner = await tenant(operator, "llmlegacy")
    await _store_legacy(
        operator, owner.scope.workspace_id, {"anthropic": {"base_url": "https://gateway.example.com"}}
    )
    view = (await operator.client.get(LLM, headers=owner.headers)).json()["providers"]["anthropic"]
    assert view["api_key_source"] is None and not view["configured"]
    effective = await operator.state.service(SETTINGS).effective(owner.scope.workspace_id)
    assert effective.anthropic.api_key is None
    tested = await operator.client.post(f"{LLM}/test", json={"provider": "anthropic"}, headers=owner.headers)
    assert tested.json()["checks"][0]["status"] == "not_configured" and health_calls == []


@pytest.mark.parametrize(
    "base_url",
    [
        "http://localhost@evil.com",
        "https://user:pass@gateway.example.com",
        "https://gateway.example.com/v1?redirect=x",
        "http://gateway.example.com",
        "https://127.0.0.1",
        "https://[::1]:8443",
        "https://169.254.169.254/latest",
        "https://metadata.internal",
        "https://gateway.example.com\\@evil.com",
    ],
)
async def test_base_urls_are_parsed_not_prefix_matched(operator: Harness, base_url: str) -> None:
    owner = await tenant(operator, "llmurl")
    body = {"providers": {"openai_compat": {"base_url": base_url, "api_key": WORKSPACE_KEY}}}
    response = await operator.client.put(LLM, json=body, headers=owner.headers)
    assert response.status_code == 422, (base_url, response.text)


async def test_plain_http_needs_the_dev_allowlist(make_harness: HarnessFactory, monkeypatch) -> None:
    monkeypatch.setenv(
        "GH_SECURITY__TOKEN_ENCRYPTION_KEYS", json.dumps([{"kid": "k1", "key": ENCRYPTION_KEY}])
    )
    h = await make_harness(llm={"workspace_base_url_dev_hosts": ["localhost"]})
    owner = await tenant(h, "llmdev")
    body = {
        "providers": {"openai_compat": {"base_url": "http://localhost:11434/v1", "api_key": WORKSPACE_KEY}}
    }
    assert (await h.client.put(LLM, json=body, headers=owner.headers)).status_code == 200


async def test_connection_test_and_runs_refuse_hosts_that_resolve_privately(
    operator: Harness, health_calls, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await tenant(operator, "llmrebind")
    body = {
        "providers": {
            "openai_compat": {"base_url": "https://gateway.example.com/v1", "api_key": WORKSPACE_KEY}
        }
    }
    assert (await operator.client.put(LLM, json=body, headers=owner.headers)).status_code == 200

    async def private(host: str, port: int) -> list[str]:
        return ["10.0.0.5"]

    monkeypatch.setattr(LLMConfigService, "guard", SsrfGuard(private))
    tested = await operator.client.post(
        f"{LLM}/test", json={"provider": "openai_compat"}, headers=owner.headers
    )
    check = tested.json()["checks"][0]
    assert (
        check["status"] == "failed"
        and "public address" in check["detail"]
        and "10.0.0.5" not in check["detail"]
    )
    assert health_calls == []
    with pytest.raises(Exception, match="public address"):
        await operator.state.service(SETTINGS).effective(owner.scope.workspace_id)
