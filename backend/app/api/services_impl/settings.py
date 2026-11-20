import base64
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy.orm.attributes import flag_modified

from app.api.errors import FieldError, NotFound, Unavailable, ValidationFailed
from app.api.schemas.settings import (
    LLMProviderIn,
    LLMProviderOut,
    LLMSettingsIn,
    LLMSettingsOut,
    ProviderName,
    TierModels,
)
from app.api.services import SETTINGS, api_service_registry
from app.api.services_impl.common import ServiceBase
from app.core.config import LLMSettings
from app.core.container import Container
from app.core.security import InvalidBindingError, InvalidCiphertextError, SecretCipher
from app.core.tenancy import TenantScope
from app.models.tenancy import Workspace

LLM_KEY = "llm"
PROVIDERS: tuple[ProviderName, ...] = ("anthropic", "openai_compat")
TIERS = ("low", "medium", "high")


def binding(workspace_id: UUID, provider: str) -> str:
    # ties a stored key to its workspace and provider so a copied ciphertext is useless elsewhere
    return f"workspace:{workspace_id}:llm:{provider}"


def invalid(message: str, field: str) -> ValidationFailed:
    return ValidationFailed(
        message, errors=[FieldError(loc=["body", *field.split(".")], msg=message, type="invalid")]
    )


class LLMConfigService(ServiceBase):
    """per-workspace llm provider overrides on top of the deployment settings; keys are write-only."""

    def cipher(self) -> SecretCipher:
        try:
            return self.container.cipher
        except ValueError:
            raise Unavailable("set GH_SECURITY__TOKEN_ENCRYPTION_KEYS before storing provider keys") from None

    async def _workspace(self, session: Any, scope: TenantScope) -> Workspace:
        workspace = await session.get(Workspace, scope.workspace_id)
        if workspace is None or workspace.deleted_at is not None:
            raise NotFound("workspace not found")
        return workspace

    def _view(self, stored: dict[str, Any]) -> LLMSettingsOut:
        base = self.settings.llm
        providers = {}
        for name in PROVIDERS:
            env = base.provider(name)
            own = stored.get("providers", {}).get(name, {})
            models = own.get("models", {})
            workspace_key = "api_key" in own
            key_source = "workspace" if workspace_key else "environment" if env.api_key is not None else None
            base_url = own.get("base_url", env.base_url)
            providers[name] = LLMProviderOut(
                kind=env.kind,
                base_url=base_url,
                models=TierModels(**{tier: models.get(tier, env.tier(tier).model) for tier in TIERS}),
                api_key_set=key_source is not None,
                api_key_source=key_source,
                configured=key_source is not None and (env.kind == "anthropic" or base_url is not None),
            )
        updated = stored.get("updated_at")
        return LLMSettingsOut(
            enabled=stored.get("enabled", base.enabled),
            provider_order=stored.get("provider_order", list(base.provider_order)),
            providers=providers,
            updated_at=datetime.fromisoformat(updated) if updated else None,
        )

    async def llm(self, scope: TenantScope) -> LLMSettingsOut:
        async with self.session_factory() as session:
            workspace = await self._workspace(session, scope)
        return self._view((workspace.settings or {}).get(LLM_KEY, {}))

    async def put_llm(self, scope: TenantScope, body: LLMSettingsIn) -> LLMSettingsOut:
        async with self.session_factory() as session, session.begin():
            workspace = await self._workspace(session, scope)
            settings = dict(workspace.settings or {})
            stored = dict(settings.get(LLM_KEY, {}))
            if body.enabled is not None:
                stored["enabled"] = body.enabled
            if body.provider_order is not None:
                stored["provider_order"] = list(body.provider_order)
            providers = dict(stored.get("providers", {}))
            for name, change in body.providers.items():
                providers[name] = self._apply(scope.workspace_id, name, dict(providers.get(name, {})), change)
            stored["providers"] = providers
            stored["updated_at"] = self.clock.now().isoformat()
            settings[LLM_KEY] = stored
            workspace.settings = settings
            flag_modified(workspace, "settings")
        return self._view(stored)

    def _apply(
        self, workspace_id: UUID, name: str, own: dict[str, Any], change: LLMProviderIn
    ) -> dict[str, Any]:
        if change.api_key is not None and change.clear_api_key:
            raise invalid("send either api_key or clear_api_key", f"providers.{name}")
        if "base_url" in change.model_fields_set:
            own["base_url"] = change.base_url
        if change.models is not None:
            models = dict(own.get("models", {}))
            for tier in change.models.model_fields_set:
                models[tier] = getattr(change.models, tier)
            own["models"] = models
        if change.clear_api_key:
            own.pop("api_key", None)
        if change.api_key is not None:
            kid, ciphertext = self.cipher().encrypt(
                change.api_key.get_secret_value().encode(), binding=binding(workspace_id, name)
            )
            own["api_key"] = {"kid": kid, "ciphertext": base64.b64encode(ciphertext).decode()}
        env = self.settings.llm.provider(name)
        has_key = "api_key" in own or env.api_key is not None
        if env.kind == "openai_compatible" and has_key and own.get("base_url", env.base_url) is None:
            raise invalid("an OpenAI-compatible provider needs a base_url", f"providers.{name}.base_url")
        return own

    async def effective(self, workspace_id: UUID) -> LLMSettings:
        """the deployment llm settings with this workspace's overrides and decrypted keys applied."""
        async with self.session_factory() as session:
            workspace = await self._workspace(session, TenantScope(workspace_id=workspace_id))
        stored = (workspace.settings or {}).get(LLM_KEY, {})
        update: dict[str, Any] = {}
        for name in PROVIDERS:
            own = stored.get("providers", {}).get(name)
            if not own:
                continue
            provider = self.settings.llm.provider(name)
            changes: dict[str, Any] = {}
            if "base_url" in own:
                changes["base_url"] = own["base_url"]
            if "api_key" in own:
                changes["api_key"] = SecretStr(self._decrypt(workspace_id, name, own["api_key"]))
            for tier, model in own.get("models", {}).items():
                changes[tier] = provider.tier(tier).model_copy(update={"model": model})
            update[name] = provider.model_copy(update=changes)
        for field in ("enabled", "provider_order"):
            if field in stored:
                update[field] = stored[field]
        return self.settings.llm.model_copy(update=update)

    def _decrypt(self, workspace_id: UUID, name: str, sealed: dict[str, str]) -> str:
        try:
            plaintext = self.cipher().decrypt(
                base64.b64decode(sealed["ciphertext"]), binding=binding(workspace_id, name)
            )
        except (InvalidCiphertextError, InvalidBindingError):
            raise Unavailable(f"the stored {name} key cannot be decrypted; enter it again") from None
        return plaintext.decode()


@api_service_registry.register(SETTINGS)
def build_settings_service(container: Container) -> LLMConfigService:
    return LLMConfigService(container)
