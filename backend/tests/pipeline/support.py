import uuid
from typing import Any

from sqlalchemy import delete, insert

from app.core.config import Settings
from app.core.container import Container, build_container
from app.core.tenancy import TenantScope
from app.domain.runs import RunKind, RunStatus, RunTrigger
from app.models.tenancy import Client, Workspace
from app.pipeline.executor import RunExecutor
from app.pipeline.registry import PipelineCatalog
from app.pipeline.runs import RunRequest
from app.pipeline.wiring import PipelineServices, build_pipeline_services
from tests.conftest import make_settings

FAST_PIPELINE = {
    "cancel_poll_s": 0.05,
    "heartbeat_s": 0.2,
    "event_flush_interval_s": 0.01,
    "progress_min_interval_s": 0.0,
}


def fast_settings(**overrides: Any) -> Settings:
    pipeline = {**FAST_PIPELINE, **overrides.pop("pipeline", {})}
    return make_settings(pipeline=pipeline, **overrides)


def make_container(settings: Settings | None = None) -> Container:
    return build_container(settings or fast_settings())


async def create_tenant(container: Container, label: str = "apollo", **client: Any) -> TenantScope:
    async with container.session_factory() as session, session.begin():
        workspace_id = (
            await session.execute(
                insert(Workspace)
                .values(name=label, slug=f"{label}-{uuid.uuid4().hex[:8]}")
                .returning(Workspace.id)
            )
        ).scalar_one()
        values = {
            "name": label,
            "slug": label,
            "website_url": f"https://{label}.example",
            "website_domain": f"{label}.example",
            "country": "IN",
            "language": "en",
            "timezone": "Asia/Kolkata",
            "lifecycle": "active",
            **client,
        }
        client_id = (
            await session.execute(
                insert(Client).values(workspace_id=workspace_id, **values).returning(Client.id)
            )
        ).scalar_one()
    return TenantScope(workspace_id=workspace_id, client_id=client_id)


async def drop_workspace(container: Container, workspace_id: uuid.UUID) -> None:
    async with container.session_factory() as session, session.begin():
        await session.execute(delete(Workspace).where(Workspace.id == workspace_id))


def services(container: Container, catalog: PipelineCatalog) -> PipelineServices:
    return build_pipeline_services(container, catalog=catalog)


async def run_pipeline(
    container: Container,
    pipeline_services: PipelineServices,
    scope: TenantScope,
    *,
    kind: RunKind = RunKind.analysis,
    owner: str = "worker-a",
    **request: Any,
) -> tuple[uuid.UUID, RunStatus | None]:
    result = await pipeline_services.runs.request_run(
        scope, RunRequest(kind=kind, trigger=RunTrigger.user, **request)
    )
    executor = RunExecutor(
        container=container, runs=pipeline_services.runs, publisher=pipeline_services.publisher
    )
    status = await executor.execute(result.run.id, result.run.attempt, owner)
    return result.run.id, status
