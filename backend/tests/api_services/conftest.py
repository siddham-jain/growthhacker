import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from cryptography.fernet import Fernet
from pydantic import BaseModel
from sqlalchemy import delete, insert

import app.api.services_impl  # noqa: F401  (registers the service implementations)
from app.api import ids
from app.api.services_impl.settings import LLMConfigService
from app.core.tenancy import TenantScope
from app.domain.profile import BusinessProfile
from app.domain.runs import RunStatus
from app.models.competitors import BrandChannelResolution
from app.models.runs import ActiveResultSet
from app.models.youtube_cache import YtChannel
from app.pipeline.artifacts import ArtifactExpiry, PgArtifactStore
from app.ports.artifacts import ArtifactKey
from app.services.website_intel.inputs.ssrf import SsrfGuard
from app.services.website_intel.persistence import WebsiteIntelStore
from tests.api.conftest import (  # noqa: F401  (fixtures shared with 11's api tests)
    Account,
    Harness,
    HarnessFactory,
    _reset_sse_exit_flag,
    make_harness,
)
from tests.pipeline.support import run_pipeline

pytestmark = pytest.mark.db

ENCRYPTION_KEY = Fernet.generate_key().decode()


@dataclass
class Tenant:
    account: Account
    client: dict[str, Any]
    scope: TenantScope

    @property
    def headers(self) -> dict[str, str]:
        return self.account.headers

    @property
    def path(self) -> str:
        return f"/api/v1/clients/{self.client['id']}"


@pytest.fixture(autouse=True)
async def _shared_cache_cleanup(make_harness: HarnessFactory) -> AsyncIterator[None]:  # noqa: F811
    # api tests commit; rows they leave in the cross-tenant cache tables must not leak into other suites
    started = datetime.now(UTC) - timedelta(seconds=1)
    yield
    harness = await make_harness()
    async with harness.container.session_factory() as session, session.begin():
        await session.execute(
            delete(BrandChannelResolution).where(BrandChannelResolution.verified_at >= started)
        )
        await session.execute(delete(YtChannel).where(YtChannel.first_seen_at >= started))


@pytest.fixture
async def harness(make_harness: HarnessFactory, monkeypatch: pytest.MonkeyPatch) -> Harness:  # noqa: F811
    # provider keys are sealed with the token encryption keys; the harness pins only the pepper
    monkeypatch.setenv(
        "GH_SECURITY__TOKEN_ENCRYPTION_KEYS", json.dumps([{"kid": "k1", "key": ENCRYPTION_KEY}])
    )
    return await make_harness()


async def tenant(harness: Harness, label: str = "apollo", **client: Any) -> Tenant:
    account = await harness.account(label)
    created = await harness.create_client(account, **client)
    scope = TenantScope(
        workspace_id=ids.decode(ids.WORKSPACE, account.workspace_id),
        client_id=ids.decode(ids.CLIENT, created["id"]),
    )
    return Tenant(account=account, client=created, scope=scope)


async def finished_run(harness: Harness, scope: TenantScope) -> uuid.UUID:
    run_id, status = await run_pipeline(harness.container, harness.state.pipeline, scope)
    assert status is RunStatus.succeeded
    return run_id


def artifacts(harness: Harness, scope: TenantScope) -> PgArtifactStore:
    settings = harness.settings
    return PgArtifactStore(
        harness.container.session_factory,
        scope,
        harness.container.clock,
        ArtifactExpiry(settings.policy.retention_days, settings.retention.run_artifacts_ttl_days),
    )


async def put_artifact(
    harness: Harness, scope: TenantScope, run_id: uuid.UUID, key: ArtifactKey, value: BaseModel
) -> None:
    await artifacts(harness, scope).put(run_id, key, value, fingerprint=f"test-{key.name}")


async def publish(
    harness: Harness, scope: TenantScope, run_id: uuid.UUID, windows: tuple[int, ...] = (7, 30, 90)
) -> None:
    async with harness.container.session_factory() as session, session.begin():
        for window in windows:
            await session.execute(
                insert(ActiveResultSet).values(
                    workspace_id=scope.workspace_id,
                    client_id=scope.client_id,
                    window_days=window,
                    run_id=run_id,
                    published_at=harness.container.clock.now(),
                    stale_reasons=[],
                )
            )


async def seed_profile(harness: Harness, scope: TenantScope, profile: BusinessProfile) -> BusinessProfile:
    stored = profile.model_copy(update={"client_id": scope.client_id, "status": "awaiting_confirmation"})
    async with harness.container.session_factory() as session, session.begin():
        await WebsiteIntelStore(session, scope).save_profile(
            stored, created_by="system", llm_usage={}, prompt_versions={}
        )
    return stored


async def public_resolver(host: str, port: int) -> list[str]:
    return ["93.184.216.34"]


@pytest.fixture(autouse=True)
def _public_llm_base_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    # workspace base_urls are ssrf-checked; the example gateway hosts must not need real dns here
    monkeypatch.setattr(LLMConfigService, "guard", SsrfGuard(public_resolver))
