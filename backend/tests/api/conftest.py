import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from sqlalchemy import delete
from sse_starlette.sse import AppStatus

from app.api.app import build_state, create_app
from app.api.cli import bootstrap
from app.api.state import ApiState
from app.core.config import Settings
from app.core.container import Container
from app.domain.runs import RunKind
from app.models.tenancy import User, Workspace
from tests.pipeline.fakes import Probe, catalog, fake_stage, pipeline
from tests.pipeline.support import fast_settings, make_container

PEPPER = "test-pepper-not-a-secret"
PASSWORD = "correct-horse-battery"


@dataclass
class Account:
    workspace_id: str
    api_key: str
    email: str

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}


@dataclass
class Harness:
    client: httpx.AsyncClient
    state: ApiState
    container: Container
    settings: Settings
    probe: Probe
    accounts: list[Account] = field(default_factory=list)

    async def account(self, label: str) -> Account:
        email = f"{label}-{uuid.uuid4().hex[:6]}@example.com"
        result = await bootstrap(
            self.settings,
            workspace_name=f"{label}-{uuid.uuid4().hex[:6]}",
            email=email,
            password=PASSWORD,
            auto_confirm=False,
        )
        account = Account(workspace_id=result.workspace_id, api_key=result.api_key, email=email)
        self.accounts.append(account)
        return account

    async def create_client(
        self, account: Account, name: str = "Apollo Hospitals", **body: Any
    ) -> dict[str, Any]:
        payload = {
            "name": name,
            "website_url": f"https://{uuid.uuid4().hex[:8]}.example.com",
            "market": {"country": "IN", "language": "en", "timezone": "Asia/Kolkata"},
            "start_onboarding": False,
            **body,
        }
        response = await self.client.post(
            "/api/v1/clients",
            json=payload,
            headers={**account.headers, "Idempotency-Key": uuid.uuid4().hex},
        )
        assert response.status_code == 201, response.text
        return response.json()["client"]


HarnessFactory = Callable[..., Awaitable[Harness]]


@pytest.fixture(autouse=True)
def _reset_sse_exit_flag() -> None:
    # sse-starlette keeps a process-wide exit flag that a previous app shutdown may have set
    AppStatus.should_exit = False


@pytest.fixture
async def make_harness(migrated_database: str) -> AsyncIterator[HarnessFactory]:
    created: list[Harness] = []

    async def factory(**overrides: Any) -> Harness:
        api = {"session_cookie_secure": False, **overrides.pop("api", {})}
        settings = fast_settings(security={"hash_pepper": PEPPER}, api=api, **overrides)
        container = make_container(settings)
        probe = Probe()
        test_catalog = catalog(
            [fake_stage("a", probe)],
            [pipeline(("a",)), pipeline(("a",), name="onboarding", kind=RunKind.onboarding)],
        )
        state = build_state(container, catalog=test_catalog)
        app = create_app(container=container)
        app.state.api = state
        await state.hub.start()
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        harness = Harness(client=client, state=state, container=container, settings=settings, probe=probe)
        created.append(harness)
        return harness

    yield factory
    for harness in created:
        await harness.client.aclose()
        await harness.state.hub.stop()
        async with harness.container.session_factory() as session, session.begin():
            for account in harness.accounts:
                await session.execute(
                    delete(Workspace).where(
                        Workspace.id == uuid.UUID(hex=account.workspace_id.removeprefix("ws_"))
                    )
                )
                await session.execute(delete(User).where(User.email == account.email))
        await harness.container.aclose()


@pytest.fixture
async def harness(make_harness: HarnessFactory) -> Harness:
    return await make_harness()
