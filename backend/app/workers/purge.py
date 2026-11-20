from typing import Literal
from uuid import UUID

from sqlalchemy import delete, exists, select

from app.core.db import SessionFactory
from app.domain.runs import ClientLifecycle
from app.models.runs import PipelineRun
from app.models.tenancy import Client
from app.pipeline.runs import ACTIVE_VALUES

PurgeOutcome = Literal["purged", "gone", "not_deleting", "run_active"]


async def purge_client_rows(session_factory: SessionFactory, client_id: UUID) -> PurgeOutcome:
    """deletes a client marked deleting once no run is active; tenant rows cascade, shared cache stays."""
    async with session_factory() as session, session.begin():
        client = (
            await session.execute(select(Client).where(Client.id == client_id).with_for_update())
        ).scalar_one_or_none()
        if client is None:
            return "gone"
        if client.lifecycle != ClientLifecycle.deleting.value:
            return "not_deleting"
        active = await session.execute(
            select(exists().where(PipelineRun.client_id == client_id, PipelineRun.status.in_(ACTIVE_VALUES)))
        )
        if active.scalar_one():
            return "run_active"
        await session.execute(delete(Client).where(Client.id == client_id))
    return "purged"
