from datetime import UTC, datetime

import pytest
from sqlalchemy import insert, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import TenantScope
from app.models.base import Base, Zone
from app.models.runs import PipelineRun
from app.models.tenancy import Client
from app.repositories.tenant import TenantMismatchError, TenantRepository, tenant_repository_registry
from app.repositories.tenant.clients import ClientRepository
from app.repositories.tenant.runs import PipelineRunRepository, RunEventRepository
from tests.foundation.factories import create_tenant, insert_row

pytestmark = pytest.mark.db

NOW = datetime(2026, 11, 20, 12, tzinfo=UTC)


def _tenant_models() -> list[type[Base]]:
    return sorted(
        (mapper.class_ for mapper in Base.registry.mappers if mapper.class_.__zone__ is Zone.tenant),
        key=lambda model: model.__tablename__,
    )


@pytest.fixture
async def tenants(session: AsyncSession) -> tuple[TenantScope, TenantScope]:
    return await create_tenant(session, "apollo"), await create_tenant(session, "fortis")


@pytest.mark.parametrize("model", _tenant_models(), ids=lambda model: model.__tablename__)
async def test_scoped_repository_never_returns_another_tenants_rows(
    model: type[Base], session: AsyncSession, tenants: tuple[TenantScope, TenantScope]
) -> None:
    tenant_a, tenant_b = tenants
    if model is Client:
        pytest.skip("covered by the client repository test")
    key_columns = [column.name for column in inspect(model).primary_key]
    parents: dict = {}
    created = {}
    for scope in tenants:
        row = await insert_row(session, model.__table__, scope, parents=parents)
        created[scope] = tuple(row[name] for name in key_columns)
    repository_type = tenant_repository_registry.all().get(model.__tablename__) or TenantRepository.for_model(
        model
    )
    repository_b = repository_type(session, tenant_b)
    rows_b = await repository_b.list(limit=1000)
    assert rows_b, model.__tablename__
    assert {row.workspace_id for row in rows_b} == {tenant_b.workspace_id}
    key_a = created[tenant_a] if len(key_columns) > 1 else created[tenant_a][0]
    assert await repository_b.get(key_a) is None
    assert await repository_b.delete(key_a) is False
    assert await repository_type(session, tenant_a).get(key_a) is not None


async def test_client_repository_scopes_by_workspace(
    session: AsyncSession, tenants: tuple[TenantScope, TenantScope]
) -> None:
    tenant_a, tenant_b = tenants
    workspace_b = ClientRepository(session, TenantScope(workspace_id=tenant_b.workspace_id))
    assert await workspace_b.get(tenant_a.client_id) is None
    assert [client.id for client in await workspace_b.list_active()] == [tenant_b.client_id]
    assert await workspace_b.get_by_slug("apollo") is None


async def test_add_rejects_entities_from_another_tenant(
    session: AsyncSession, tenants: tuple[TenantScope, TenantScope]
) -> None:
    tenant_a, tenant_b = tenants
    repository = PipelineRunRepository(session, tenant_b)
    stray = PipelineRun(
        workspace_id=tenant_a.workspace_id,
        client_id=tenant_a.client_id,
        kind="analysis",
        trigger="user",
        config_hash="h",
        queue="gh:interactive",
    )
    with pytest.raises(TenantMismatchError):
        repository.add(stray)


async def test_composite_fk_rejects_cross_workspace_client(
    session: AsyncSession, tenants: tuple[TenantScope, TenantScope]
) -> None:
    tenant_a, tenant_b = tenants
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await session.execute(
                insert(PipelineRun).values(
                    workspace_id=tenant_b.workspace_id,
                    client_id=tenant_a.client_id,
                    kind="analysis",
                    trigger="user",
                    config_hash="h",
                    queue="gh:interactive",
                )
            )


async def test_run_requests_are_idempotent_and_single_active(
    session: AsyncSession, tenants: tuple[TenantScope, TenantScope]
) -> None:
    tenant_a, _ = tenants
    runs = PipelineRunRepository(session, tenant_a)
    base = {"kind": "analysis", "trigger": "user", "config_hash": "h1", "queue": "gh:interactive"}
    first = await runs.request_run({**base, "idempotency_key": "key-1"})
    assert first.created
    replay = await runs.request_run({**base, "idempotency_key": "key-1"})
    assert replay.reason == "idempotency_key" and replay.run.id == first.run.id
    concurrent = await runs.request_run({**base, "idempotency_key": "key-2"})
    assert concurrent.reason == "active_run" and concurrent.run.id == first.run.id

    assert await runs.transition(first.run.id, from_statuses=["queued"], to_status="running", started_at=NOW)
    assert not await runs.transition(first.run.id, from_statuses=["queued"], to_status="running")
    assert await runs.request_cancel(first.run.id, at=NOW)
    assert await runs.supersede(first.run.id, by_run_id=first.run.id, at=NOW)
    follow_up = await runs.request_run({**base, "idempotency_key": "key-3"})
    assert follow_up.created


async def test_pins_and_events(session: AsyncSession, tenants: tuple[TenantScope, TenantScope]) -> None:
    tenant_a, tenant_b = tenants
    runs = PipelineRunRepository(session, tenant_a)
    run = (
        await runs.request_run({"kind": "onboarding", "trigger": "user", "config_hash": "h", "queue": "q"})
    ).run
    await runs.pin_channels(run.id, ["UCb", "UCa"])
    assert await runs.pin_channels(run.id, ["UCa", "UCc"]) == ["UCa", "UCb", "UCc"]

    events = RunEventRepository(session, tenant_a)
    seqs = await events.append(
        run.id, [{"type": "run.queued", "message": "queued"}, {"type": "run.started", "message": "go"}]
    )
    assert seqs == [1, 2]
    assert [event.seq for event in await events.replay(run.id, after_seq=1)] == [2]
    assert await RunEventRepository(session, tenant_b).replay(run.id) == []
    with pytest.raises(LookupError):
        await RunEventRepository(session, tenant_b).append(run.id, [{"type": "notice", "message": "x"}])
