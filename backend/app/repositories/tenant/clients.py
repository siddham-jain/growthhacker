import uuid
from collections.abc import Sequence

from sqlalchemy import select

from app.models.tenancy import Client
from app.repositories.tenant.base import TenantRepository


class ClientRepository(TenantRepository[Client]):
    model = Client
    client_column_name = "id"

    async def get_by_slug(self, slug: str) -> Client | None:
        statement = self._scoped(select(Client).where(Client.slug == slug, Client.deleted_at.is_(None)))
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def get_by_own_channel(self, channel_id: str) -> Client | None:
        statement = self._scoped(
            select(Client).where(Client.own_channel_id == channel_id, Client.deleted_at.is_(None))
        )
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def list_active(self) -> Sequence[Client]:
        statement = self._scoped(select(Client).where(Client.deleted_at.is_(None))).order_by(
            Client.created_at
        )
        return (await self.session.execute(statement)).scalars().all()

    async def exists(self, client_id: uuid.UUID) -> bool:
        return await self.get(client_id) is not None
