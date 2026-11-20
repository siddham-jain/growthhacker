import uuid

from sqlalchemy import select

from app.models.tenancy import OAuthToken
from app.repositories.tenant.base import TenantRepository


class OAuthTokenRepository(TenantRepository[OAuthToken]):
    model = OAuthToken

    async def active_for_client(self, provider: str = "google") -> OAuthToken | None:
        statement = self._scoped(
            select(OAuthToken).where(OAuthToken.provider == provider, OAuthToken.status == "active")
        )
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def viewer_may_see(self, token_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        token = await self.get(token_id)
        return token is not None and (
            user_id == token.granted_by_user_id or user_id in token.approved_viewer_user_ids
        )
