import base64
from datetime import datetime
from typing import Any
from uuid import UUID

import anyio
from pydantic import SecretStr
from sqlalchemy.orm.attributes import flag_modified

from app.adapters.llm.health import check_llm_health
from app.adapters.llm.providers.anthropic_provider import SAMPLING_PARAMS
from app.api.errors import FieldError, NotFound, Unavailable, ValidationFailed
from app.api.schemas.settings import (
    LLMProviderIn,
    LLMProviderOut,
    LLMRouteCheck,
    LLMSettingsIn,
    LLMSettingsOut,
    LLMTestOut,
    OpenAICompatOptions,
    ProviderName,
    TierOut,
    TiersOut,
)
from app.api.services import SETTINGS, api_service_registry
from app.api.services_impl.common import ServiceBase
from app.core.config import TIERS, LLMProviderSettings, LLMSettings, OpenAICompatSettings
from app.core.container import Container
from app.core.security import InvalidBindingError, InvalidCiphertextError, SecretCipher
from app.core.tenancy import TenantScope
from app.models.tenancy import Workspace

LLM_KEY = "llm"
PROVIDERS: tuple[ProviderName, ...] = ("anthropic", "openai_compat")
PROVIDER_FIELDS = ("base_url", "max_concurrency", "connect_timeout_s")
# anthropic takes extra request params too (its adapter drops sampling params)
ANTHROPIC_FIELDS = ("extra_params",)
OPENAI_FIELDS = (
    "extra_params",
    "extra_body",
    "structured_dialect",
    "token_param",
    "supports_reasoning_effort",
)
TIER_FIELDS = ("model", "max_tokens", "timeout_s", "max_retries", "effort")
TEST_TIMEOUT_S = 30


def binding(workspace_id: UUID, provider: str) -> str:
    # ties a stored key to its workspace and provider so a copied ciphertext is useless elsewhere
    return f"workspace:{workspace_id}:llm:{provider}"


def invalid(message: str, field: str) -> ValidationFailed:
    return ValidationFailed(
        message, errors=[FieldError(loc=["body", *field.split(".")], msg=message, type="invalid")]
    )


def overlay(
    env: LLMProviderSettings, own: dict[str, Any], api_key: SecretStr | None = None
) -> LLMProviderSettings:
    """one provider's deployment settings with a workspace's stored overrides applied."""
    fields = PROVIDER_FIELDS + (OPENAI_FIELDS if isinstance(env, OpenAICompatSettings) else ANTHROPIC_FIELDS)
    changes: dict[str, Any] = {name: own[name] for name in fields if name in own}
    for tier, values in own.get("tiers", {}).items():
        changes[tier] = env.tier(tier).model_copy(update=values)
    if api_key is not None:
        changes["api_key"] = api_key
    return env.model_copy(update=changes)


def overridden(own: dict[str, Any]) -> list[str]:
    paths = [name for name in (*PROVIDER_FIELDS, *OPENAI_FIELDS, "enabled", "api_key") if name in own]
    for tier, values in own.get("tiers", {}).items():
        paths.extend(f"tiers.{tier}.{name}" for name in values)
    return sorted(paths)


def tier_view(provider: LLMProviderSettings) -> TiersOut:
    return TiersOut(
        **{tier: TierOut(**provider.tier(tier).model_dump(include=set(TIER_FIELDS))) for tier in TIERS}
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

    async def _stored(self, workspace_id: UUID) -> dict[str, Any]:
        async with self.session_factory() as session:
            workspace = await self._workspace(session, TenantScope(workspace_id=workspace_id))
        return (workspace.settings or {}).get(LLM_KEY, {})

    def _view(self, stored: dict[str, Any]) -> LLMSettingsOut:
        base = self.settings.llm
        providers = {}
        for name in PROVIDERS:
            env = base.provider(name)
            own = stored.get("providers", {}).get(name, {})
            provider = overlay(env, own)
            key_source = (
                "workspace" if "api_key" in own else "environment" if env.api_key is not None else None
            )
            openai = None
            if isinstance(provider, OpenAICompatSettings):
                openai = OpenAICompatOptions(**provider.model_dump(include=set(OPENAI_FIELDS)))
            providers[name] = LLMProviderOut(
                kind=provider.kind,
                enabled=own.get("enabled", True),
                base_url=provider.base_url,
                tiers=tier_view(provider),
                max_concurrency=provider.max_concurrency,
                connect_timeout_s=provider.connect_timeout_s,
                openai=openai,
                api_key_set=key_source is not None,
                api_key_source=key_source,
                configured=key_source is not None
                and (provider.kind == "anthropic" or provider.base_url is not None),
                overridden=overridden(own),
            )
        updated = stored.get("updated_at")
        return LLMSettingsOut(
            enabled=stored.get("enabled", base.enabled),
            provider_order=stored.get("provider_order", list(base.provider_order)),
            providers=providers,
            updated_at=datetime.fromisoformat(updated) if updated else None,
        )

    async def llm(self, scope: TenantScope) -> LLMSettingsOut:
        return self._view(await self._stored(scope.workspace_id))

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
        field = f"providers.{name}"
        if change.api_key is not None and change.clear_api_key:
            raise invalid("send either api_key or clear_api_key", field)
        env = self.settings.llm.provider(name)
        sent = change.model_fields_set
        if not isinstance(env, OpenAICompatSettings):
            unsupported = sorted((set(OPENAI_FIELDS) - set(ANTHROPIC_FIELDS)) & sent)
            if unsupported:
                raise invalid(f"{', '.join(unsupported)} apply to the OpenAI-compatible provider only", field)
            sampling = sorted(SAMPLING_PARAMS & set(change.extra_params or {}))
            if sampling:
                raise invalid(f"anthropic does not accept sampling params: {', '.join(sampling)}", field)
        if change.reset:
            own = {"api_key": own["api_key"]} if "api_key" in own else {}
        if change.enabled is not None:
            own["enabled"] = change.enabled
        # a null field reverts to the deployment default
        for key in (*PROVIDER_FIELDS, *OPENAI_FIELDS):
            if key in sent and getattr(change, key) is None:
                own.pop(key, None)
            elif key in sent:
                own[key] = getattr(change, key)
        if change.tiers is not None:
            tiers = {tier: dict(values) for tier, values in own.get("tiers", {}).items()}
            for tier in change.tiers.model_fields_set:
                tier_change = getattr(change.tiers, tier)
                if tier_change is None:
                    tiers.pop(tier, None)
                    continue
                values = tiers.setdefault(tier, {})
                for key in tier_change.model_fields_set:
                    value = getattr(tier_change, key)
                    if value is None:
                        values.pop(key, None)
                    else:
                        values[key] = value
            own["tiers"] = {tier: values for tier, values in tiers.items() if values}
        if change.clear_api_key:
            own.pop("api_key", None)
        if change.api_key is not None:
            kid, ciphertext = self.cipher().encrypt(
                change.api_key.get_secret_value().encode(), binding=binding(workspace_id, name)
            )
            own["api_key"] = {"kid": kid, "ciphertext": base64.b64encode(ciphertext).decode()}
        has_key = "api_key" in own or env.api_key is not None
        if env.kind == "openai_compatible" and has_key and own.get("base_url", env.base_url) is None:
            raise invalid("an OpenAI-compatible provider needs a base_url", f"{field}.base_url")
        return own

    async def effective(self, workspace_id: UUID) -> LLMSettings:
        """the deployment llm settings with this workspace's overrides and decrypted keys applied."""
        return self._effective(workspace_id, await self._stored(workspace_id))

    def _effective(self, workspace_id: UUID, stored: dict[str, Any]) -> LLMSettings:
        base = self.settings.llm
        update: dict[str, Any] = {}
        enabled_providers = set()
        for name in PROVIDERS:
            own = stored.get("providers", {}).get(name, {})
            if own.get("enabled", True):
                enabled_providers.add(name)
            if own:
                key = (
                    SecretStr(self._decrypt(workspace_id, name, own["api_key"])) if "api_key" in own else None
                )
                update[name] = overlay(base.provider(name), own, key)
        order = [
            name for name in stored.get("provider_order", base.provider_order) if name in enabled_providers
        ]
        update["provider_order"] = order
        update["enabled"] = stored.get("enabled", base.enabled) and bool(order)
        return base.model_copy(update=update)

    async def test(self, scope: TenantScope, provider: ProviderName | None) -> LLMTestOut:
        """checks each saved route's key and model ids against the provider; spends no tokens."""
        # disabled providers are checked too, so a gateway can be verified before it is switched on
        effective = self._effective(scope.workspace_id, await self._stored(scope.workspace_id))
        checks: list[LLMRouteCheck] = []
        for name in [provider] if provider else list(PROVIDERS):
            settings = effective.provider(name)
            if not settings.configured:
                detail = "add an API key" if settings.api_key is None else "add a base URL"
                checks.append(
                    LLMRouteCheck(provider=name, model=None, status="not_configured", detail=detail)
                )
                continue
            # health checks every configured provider, so the other one is masked out
            others = {
                other: effective.provider(other).model_copy(update={"api_key": None})
                for other in PROVIDERS
                if other != name
            }
            with anyio.move_on_after(TEST_TIMEOUT_S) as deadline:
                routes = await check_llm_health(effective.model_copy(update=others))
            if deadline.cancelled_caught:
                checks.append(LLMRouteCheck(provider=name, model=None, status="failed", detail="timed out"))
                continue
            checks.extend(
                LLMRouteCheck(provider=name, model=route.model, status=route.status, detail=route.detail)
                for route in routes
            )
            if not routes:
                checks.append(
                    LLMRouteCheck(
                        provider=name, model=None, status="not_configured", detail="no tier has a model"
                    )
                )
        return LLMTestOut(
            ok=any(check.status == "ok" for check in checks)
            and not any(check.status == "failed" for check in checks),
            checks=checks,
            checked_at=self.clock.now(),
        )

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
