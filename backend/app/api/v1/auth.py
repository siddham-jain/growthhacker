from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from app.api import ids
from app.api.deps import Auth, State, ip_limited, limited
from app.api.errors import PROBLEM_RESPONSES
from app.api.schemas.admin import LoginIn, MeOut, MeUser, MeWorkspace, WorkspaceSwitchIn
from app.api.schemas.common import StatusOut

router = APIRouter(tags=["auth"], responses=PROBLEM_RESPONSES)


@router.post(
    "/auth/login",
    operation_id="auth_login",
    response_model=StatusOut,
    dependencies=[Depends(ip_limited("write"))],
)
async def auth_login(body: LoginIn, request: Request, state: State) -> JSONResponse:
    issued = await state.authenticator.login(
        body.email.lower(), body.password, user_agent=request.headers.get("user-agent")
    )
    response = JSONResponse(StatusOut(status="signed_in").model_dump())
    api = state.settings.api
    response.set_cookie(
        api.session_cookie,
        issued.token,
        expires=issued.expires_at,
        httponly=True,
        secure=api.session_cookie_secure,
        samesite="lax",
        path="/",
    )
    return response


@router.post(
    "/auth/logout", operation_id="auth_logout", status_code=204, dependencies=[Depends(limited("write"))]
)
async def auth_logout(context: Auth, state: State) -> Response:
    if context.session_id is not None:
        await state.authenticator.logout(context.session_id)
    response = Response(status_code=204)
    response.delete_cookie(state.settings.api.session_cookie, path="/")
    return response


@router.post(
    "/auth/workspace",
    operation_id="auth_switch_workspace",
    response_model=StatusOut,
    dependencies=[Depends(limited("write"))],
)
async def auth_switch_workspace(body: WorkspaceSwitchIn, context: Auth, state: State) -> StatusOut:
    await state.authenticator.switch_workspace(context, ids.decode(ids.WORKSPACE, body.workspace_id))
    return StatusOut(status="switched")


@router.get("/me", operation_id="me_get", response_model=MeOut, dependencies=[Depends(limited("read"))])
async def me_get(context: Auth, state: State) -> MeOut:
    principal = context.principal
    user = None if context.user_id is None else await state.authenticator.user(context.user_id)
    if principal.kind == "user" and context.user_id is not None:
        memberships = await state.authenticator.workspaces(context.user_id)
        workspaces = [
            MeWorkspace(
                id=ids.encode(ids.WORKSPACE, workspace.id),
                name=workspace.name,
                slug=workspace.slug,
                role=role.value,
            )
            for workspace, role in memberships
        ]
    else:
        workspaces = []
    return MeOut(
        principal="api_key" if principal.kind == "api_key" else "user",
        user=None
        if user is None or principal.kind == "api_key"
        else MeUser(id=ids.encode(ids.USER, user.id), email=user.email, display_name=user.display_name),
        workspaces=workspaces,
        active_workspace_id=ids.encode_optional(ids.WORKSPACE, principal.workspace_id),
        role=principal.role.value,
    )
