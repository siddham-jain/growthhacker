from fastapi import APIRouter

from app.api.deps import Owner, State, WorkspaceScope
from app.api.errors import PROBLEM_RESPONSES
from app.api.schemas.settings import LLMSettingsIn, LLMSettingsOut
from app.api.services import SETTINGS

router = APIRouter(tags=["settings"], responses=PROBLEM_RESPONSES)


@router.get("/settings/llm", operation_id="settings_llm_get", response_model=LLMSettingsOut)
async def settings_llm_get(state: State, scope: WorkspaceScope) -> LLMSettingsOut:
    return await state.service(SETTINGS).llm(scope)


@router.put("/settings/llm", operation_id="settings_llm_put", response_model=LLMSettingsOut)
async def settings_llm_put(
    body: LLMSettingsIn, state: State, scope: WorkspaceScope, context: Owner
) -> LLMSettingsOut:
    return await state.service(SETTINGS).put_llm(scope, body)
