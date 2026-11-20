from collections.abc import AsyncIterator, Callable
from datetime import timedelta
from typing import Annotated, Any

from fastapi import Depends, Header, Path, Request
from sqlalchemy import select

from app.api import ids
from app.api.auth import AuthContext, required_scope, scope_allows
from app.api.errors import Forbidden, IdempotencyKeyRequired, InvalidCredentials, NotFound, RateLimited
from app.api.idempotency import IdempotentRequest, claim, parse_key
from app.api.state import ApiState
from app.core.tenancy import Role, TenantScope
from app.models.tenancy import Client

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def api_state(request: Request) -> ApiState:
    return request.app.state.api


State = Annotated[ApiState, Depends(api_state)]


async def authenticate(request: Request, state: State) -> AuthContext:
    cached = getattr(request.state, "auth", None)
    if cached is not None:
        return cached
    header = request.headers.get("authorization")
    if header:
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise InvalidCredentials("use 'Authorization: Bearer <api key>'")
        context = await state.authenticator.api_key(token.strip())
    else:
        token = request.cookies.get(state.settings.api.session_cookie)
        if not token:
            raise InvalidCredentials("sign in or send an api key")
        context = await state.authenticator.session(token)
        # cookie sessions need a header a cross-site form cannot set
        if request.method not in SAFE_METHODS and "x-requested-with" not in request.headers:
            raise Forbidden("cookie-authenticated writes need an X-Requested-With header")
    request.state.auth = context
    return context


Auth = Annotated[AuthContext, Depends(authenticate)]


def _subject(context: AuthContext) -> str:
    return f"{context.principal.kind}:{context.principal.id}"


async def spend_limit(request: Request, state: ApiState, subject: str, bucket: str) -> None:
    decision = await state.limiter.hit(subject, bucket)
    headers = getattr(request.state, "rate_headers", None)
    if headers is None or decision.remaining < headers["remaining"]:
        request.state.rate_headers = {
            "RateLimit-Policy": decision.policy_header(),
            "RateLimit": decision.header(),
            "remaining": decision.remaining,
        }
    if not decision.allowed:
        raise RateLimited(f"the {bucket} limit was reached", retry_after_s=decision.retry_after_s)


async def access(request: Request, state: State, context: Auth) -> AuthContext:
    """router-wide: viewers may only read, api keys only within their scopes; every call spends a bucket."""
    principal = context.principal
    if request.method not in SAFE_METHODS and not principal.role.allows(Role.editor):
        raise Forbidden("viewers can only read")
    if principal.kind == "api_key":
        route = request.scope.get("route")
        needed = required_scope(request.method, getattr(route, "path", request.url.path))
        if not scope_allows(principal.scopes, needed):
            raise Forbidden(f"this api key lacks the {needed} scope")
    await spend_limit(
        request, state, _subject(context), "read" if request.method in SAFE_METHODS else "write"
    )
    return context


Access = Annotated[AuthContext, Depends(access)]


def require_role(role: Role) -> Callable[..., Any]:
    async def dependency(context: Access) -> AuthContext:
        if not context.principal.role.allows(role):
            raise Forbidden(f"the {role.value} role is required")
        return context

    return dependency


Owner = Annotated[AuthContext, Depends(require_role(Role.owner))]


def workspace_scope(context: Access) -> TenantScope:
    if context.principal.workspace_id is None:
        raise Forbidden("you are not a member of any workspace")
    return context.principal.scope()


WorkspaceScope = Annotated[TenantScope, Depends(workspace_scope)]


async def client_scope(
    state: State, scope: WorkspaceScope, client_id: Annotated[str, Path(description="cl_… id")]
) -> TenantScope:
    # another workspace's client is indistinguishable from a missing one
    identifier = ids.decode(ids.CLIENT, client_id)
    async with state.container.session_factory() as session:
        found = (
            await session.execute(
                select(Client.id).where(
                    Client.id == identifier,
                    Client.workspace_id == scope.workspace_id,
                    Client.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
    if found is None:
        raise NotFound("client not found")
    return scope.for_client(identifier)


ClientScope = Annotated[TenantScope, Depends(client_scope)]


def limited(bucket: str) -> Callable[..., Any]:
    async def dependency(request: Request, state: State, context: Auth) -> None:
        await spend_limit(request, state, _subject(context), bucket)

    return dependency


def ip_limited(bucket: str) -> Callable[..., Any]:
    # unauthenticated endpoints (login) are limited per client address
    async def dependency(request: Request, state: State) -> None:
        host = request.client.host if request.client else "unknown"
        await spend_limit(request, state, f"ip:{host}", bucket)

    return dependency


async def run_start_limit(request: Request, state: State, scope: WorkspaceScope) -> None:
    # the client must belong to the caller before its bucket is charged, or anyone could drain it
    client = request.path_params.get("client_id")
    if client:
        client_scoped = await client_scope(state, scope, client)
        await spend_limit(request, state, f"client:{client_scoped.client_id}", "run_start_client")
    await spend_limit(request, state, f"workspace:{scope.workspace_id}", "run_start_workspace")


def idempotency(*, required_for_api_keys: bool) -> Callable[..., Any]:
    async def dependency(
        request: Request,
        state: State,
        context: Access,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> AsyncIterator[IdempotentRequest]:
        if idempotency_key is None:
            if required_for_api_keys and context.principal.kind == "api_key":
                raise IdempotencyKeyRequired("send an Idempotency-Key header with this request")
            yield IdempotentRequest()
            return
        if context.principal.workspace_id is None:
            raise Forbidden("you are not a member of any workspace")
        guard = await claim(
            state.container.session_factory,
            workspace_id=context.principal.workspace_id,
            key=parse_key(idempotency_key),
            method=request.method,
            # the concrete path, so one key reused for another client is a conflict rather than a replay
            path=request.url.path,
            body=await request.body(),
            ttl=timedelta(hours=state.settings.api.idempotency_ttl_h),
            max_body_bytes=state.settings.api.idempotency_max_body_bytes,
        )
        try:
            yield guard
        except BaseException:
            await guard.release()
            raise
        await guard.release()

    return dependency
