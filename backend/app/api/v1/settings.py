from fastapi import APIRouter

from app.api.deps import ClientScope, Owner, State, WorkspaceScope
from app.api.errors import PROBLEM_RESPONSES
from app.api.schemas.settings import (
    LLMSettingsIn,
    LLMSettingsOut,
    LLMTestIn,
    LLMTestOut,
    NotificationSettingsOut,
    NotificationSettingsPatch,
)
from app.api.services import NOTIFICATIONS, SETTINGS

router = APIRouter(tags=["settings"], responses=PROBLEM_RESPONSES)


@router.get("/settings/llm", operation_id="settings_llm_get", response_model=LLMSettingsOut)
async def settings_llm_get(state: State, scope: WorkspaceScope) -> LLMSettingsOut:
    return await state.service(SETTINGS).llm(scope)


@router.put("/settings/llm", operation_id="settings_llm_put", response_model=LLMSettingsOut)
async def settings_llm_put(
    body: LLMSettingsIn, state: State, scope: WorkspaceScope, context: Owner
) -> LLMSettingsOut:
    return await state.service(SETTINGS).put_llm(scope, body)


@router.post("/settings/llm/test", operation_id="settings_llm_test", response_model=LLMTestOut)
async def settings_llm_test(
    body: LLMTestIn, state: State, scope: WorkspaceScope, context: Owner
) -> LLMTestOut:
    return await state.service(SETTINGS).test(scope, body.provider)


@router.get(
    "/clients/{client_id}/notification-settings",
    operation_id="notification_settings_get",
    response_model=NotificationSettingsOut,
)
async def notification_settings_get(state: State, scope: ClientScope) -> NotificationSettingsOut:
    return await state.service(NOTIFICATIONS).get(scope)


@router.patch(
    "/clients/{client_id}/notification-settings",
    operation_id="notification_settings_patch",
    response_model=NotificationSettingsOut,
)
async def notification_settings_patch(
    body: NotificationSettingsPatch, state: State, scope: ClientScope
) -> NotificationSettingsOut:
    return await state.service(NOTIFICATIONS).patch(scope, body)
