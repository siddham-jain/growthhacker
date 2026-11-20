from collections import defaultdict
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.competitors import CompetitorSet
from app.models.tenancy import Client


async def clients_by_channel(
    session: AsyncSession, workspace_id: UUID | None = None, client_id: UUID | None = None
) -> dict[str, list[str]]:
    """channel -> clients whose universe holds it (own channel plus the latest confirmed set)."""
    statement = select(Client).where(Client.archived_at.is_(None))
    if workspace_id is not None:
        statement = statement.where(Client.workspace_id == workspace_id)
    if client_id is not None:
        statement = statement.where(Client.id == client_id)
    clients = (await session.execute(statement)).scalars().all()
    mapping: dict[str, set[str]] = defaultdict(set)
    for client in clients:
        if client.own_channel_id:
            mapping[client.own_channel_id].add(str(client.id))
        latest = (
            await session.execute(
                select(CompetitorSet)
                .where(CompetitorSet.client_id == client.id)
                .order_by(CompetitorSet.version.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if latest is not None:
            for channel_id in (*latest.competitor_channel_ids, *latest.niche_channel_ids):
                mapping[channel_id].add(str(client.id))
    return {channel: sorted(ids) for channel, ids in sorted(mapping.items())}
