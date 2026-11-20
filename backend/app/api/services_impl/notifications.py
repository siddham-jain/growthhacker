from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from app.api.errors import NotFound
from app.api.schemas.settings import NotificationSettingsOut, NotificationSettingsPatch
from app.api.services import NOTIFICATIONS, api_service_registry
from app.api.services_impl.common import ServiceBase
from app.core.container import Container
from app.core.tenancy import TenantScope
from app.models.tenancy import Client

NOTIFICATIONS_KEY = "notifications"
DEFAULTS: dict[str, Any] = {
    "email": None,
    "first_brief_ready": True,
    "daily_digest": False,
    "news_closing_soon": True,
}


class ClientNotificationService(ServiceBase):
    """per-client delivery choices under clients.settings; no sender exists yet, so delivery stays off."""

    def _view(self, stored: dict[str, Any]) -> NotificationSettingsOut:
        updated = stored.get("updated_at")
        return NotificationSettingsOut(
            **{key: stored.get(key, default) for key, default in DEFAULTS.items()},
            delivery_enabled=False,
            updated_at=datetime.fromisoformat(updated) if updated else None,
        )

    async def _client(self, session: Any, scope: TenantScope, *, lock: bool) -> Client:
        statement = select(Client).where(
            Client.id == scope.client_id,
            Client.workspace_id == scope.workspace_id,
            Client.deleted_at.is_(None),
        )
        client = (
            await session.execute(statement.with_for_update() if lock else statement)
        ).scalar_one_or_none()
        if client is None:
            raise NotFound("client not found")
        return client

    async def get(self, scope: TenantScope) -> NotificationSettingsOut:
        async with self.session_factory() as session:
            client = await self._client(session, scope, lock=False)
        return self._view((client.settings or {}).get(NOTIFICATIONS_KEY, {}))

    async def patch(self, scope: TenantScope, body: NotificationSettingsPatch) -> NotificationSettingsOut:
        async with self.session_factory() as session, session.begin():
            client = await self._client(session, scope, lock=True)
            settings = dict(client.settings or {})
            stored = {**settings.get(NOTIFICATIONS_KEY, {}), **body.model_dump(include=body.model_fields_set)}
            stored["updated_at"] = self.clock.now().isoformat()
            settings[NOTIFICATIONS_KEY] = stored
            client.settings = settings
            flag_modified(client, "settings")
        return self._view(stored)


@api_service_registry.register(NOTIFICATIONS)
def build_notification_service(container: Container) -> ClientNotificationService:
    return ClientNotificationService(container)
