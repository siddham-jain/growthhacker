from typing import Any
from uuid import UUID

from app.adapters.youtube.oauth_google import InvalidGrant, OAuthError
from app.api.errors import IntegrationNotConfigured
from app.api.services import YOUTUBE_OAUTH, OAuthOutcome, api_service_registry
from app.core.container import Container
from app.core.logging import get_logger
from app.core.tenancy import TenantScope
from app.services.youtube.own_analytics import OAuthConnectionService

logger = get_logger(__name__)

ENV_VARS = "GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET and GOOGLE_OAUTH_REDIRECT_URI"


class YouTubeOAuthBridge:
    """the api side of 02's oauth connection service; refuses clearly when google sign-in is not configured."""

    def __init__(self, container: Container) -> None:
        self.container = container

    def _connections(self) -> OAuthConnectionService:
        oauth = self.container.settings.google_oauth
        if not oauth.client_id or oauth.client_secret is None:
            raise IntegrationNotConfigured(
                f"YouTube sign-in is not configured on this server. Set {ENV_VARS}.",
                extra={"integration": "youtube_oauth"},
            )
        if not self.container.settings.security.token_encryption_keys:
            raise IntegrationNotConfigured(
                "YouTube sign-in needs GH_SECURITY__TOKEN_ENCRYPTION_KEYS to store tokens.",
                extra={"integration": "youtube_oauth"},
            )
        layer: Any = self.container.adapter("youtube_data_layer")
        return layer.oauth

    async def start(self, scope: TenantScope, user_id: UUID) -> str:
        return await self._connections().start(scope, user_id)

    async def complete(self, state: str, code: str, *, user_id: UUID) -> OAuthOutcome:
        connections = self._connections()
        try:
            result = await connections.complete(state, code, user_id=user_id)
        except InvalidGrant as error:
            logger.info("oauth.callback_invalid_grant", reason=str(error)[:200])
            return OAuthOutcome(status="failed", message="Google did not grant access. Please try again.")
        except OAuthError as error:
            logger.warning("oauth.callback_failed", reason=str(error)[:200])
            return OAuthOutcome(status="failed", message="We couldn't reach Google. Please try again.")
        return OAuthOutcome(status=result.status, message=result.message)

    async def disconnect(self, scope: TenantScope) -> bool:
        return await self._connections().disconnect(scope)


@api_service_registry.register(YOUTUBE_OAUTH)
def build_youtube_oauth_service(container: Container) -> YouTubeOAuthBridge:
    return YouTubeOAuthBridge(container)
