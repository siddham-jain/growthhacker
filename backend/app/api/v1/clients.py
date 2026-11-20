import contextlib
import re
from datetime import datetime
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError
from sqlalchemy import func, select, update

from app.api import ids
from app.api.deps import Access, ClientScope, State, WorkspaceScope, idempotency, run_start_limit
from app.api.errors import (
    PROBLEM_RESPONSES,
    ClientExists,
    ConfigInvalidError,
    PreconditionFailed,
)
from app.api.idempotency import IdempotentRequest
from app.api.pagination import Page
from app.api.presenters import present_client, present_clients, present_run
from app.api.schemas.clients import (
    ClientCreate,
    ClientCreatedOut,
    ClientOut,
    ClientPatch,
    ClientUpdateOut,
    DeletingOut,
    OnboardingIn,
)
from app.api.schemas.runs import RunOut
from app.api.schemas.setup import ChannelAttachIn, ChannelResolveIn, ChannelResolveOut
from app.api.services import CHANNELS
from app.api.v1.common import CHANNEL_ID, PAGING_PARAMS, json_response, only_params, run_accepted
from app.core.tenancy import TenantScope
from app.domain.runs import ClientLifecycle, RunKind, RunTrigger
from app.models.tenancy import Client, Workspace
from app.pipeline.client_settings import ClientSettings, merged, read_settings, write_settings
from app.pipeline.lifecycle import ClientLifecycleMachine, TransitionError
from app.pipeline.registry import PipelineUnavailable
from app.pipeline.runs import RunNotCancellable, RunRequest
from app.pipeline.schedules import ScheduleBook
from app.workers.queue import PURGE_CLIENT

router = APIRouter(tags=["clients"], responses=PROBLEM_RESPONSES)

_SLUG_INVALID = re.compile(r"[^a-z0-9]+")


def _slug(name: str) -> str:
    return _SLUG_INVALID.sub("-", name.lower()).strip("-")[:60] or "client"


def _domain(url: str | None) -> str | None:
    if url is None:
        return None
    host = (urlsplit(url).hostname or "").lower()
    return host.removeprefix("www.") or None


async def _resolve_channel(state: State, scope: TenantScope, channel_ref: str | None) -> str | None:
    if channel_ref is None:
        return None
    if CHANNEL_ID.match(channel_ref):
        return channel_ref
    resolved = await state.service(CHANNELS).resolve(scope, channel_ref)
    return resolved.candidates[0].channel.channel_id if resolved.candidates else None


async def _start_onboarding(
    state: State, scope: TenantScope, requested_by: str
) -> tuple[RunOut | None, str | None]:
    try:
        result = await state.pipeline.runs.request_run(
            scope, RunRequest(kind=RunKind.onboarding, trigger=RunTrigger.user, requested_by=requested_by)
        )
    except PipelineUnavailable as error:
        return None, str(error)
    return await present_run(state, result.run, with_stages=True), None


@router.post(
    "/clients",
    operation_id="clients_create",
    status_code=201,
    response_model=ClientCreatedOut,
    dependencies=[Depends(run_start_limit)],
)
async def clients_create(
    body: ClientCreate,
    state: State,
    scope: WorkspaceScope,
    context: Access,
    guard: Annotated[IdempotentRequest, Depends(idempotency(required_for_api_keys=True))],
) -> JSONResponse:
    if guard.replay is not None:
        return guard.replay
    website_url = None if body.website_url is None else str(body.website_url)
    domain = _domain(website_url)
    # handles and urls are resolved, with ownership checks, by the onboarding run's resolve_inputs stage
    channel_id = body.channel_ref if body.channel_ref and CHANNEL_ID.match(body.channel_ref) else None
    async with state.container.session_factory() as session, session.begin():
        workspace = await session.get(Workspace, scope.workspace_id)
        # demo workspaces (bootstrap --auto-confirm) skip the manual setup review for new clients
        auto_confirm = bool((workspace.settings or {}).get("auto_confirm_setup")) if workspace else False
        settings = ClientSettings(window_days=body.window_days, auto_confirm_setup=auto_confirm)
        duplicate = (
            await session.execute(
                select(Client.id).where(
                    Client.workspace_id == scope.workspace_id,
                    Client.deleted_at.is_(None),
                    (Client.website_domain == domain)
                    if domain
                    else (Client.own_channel_id == channel_id)
                    if channel_id
                    else (func.lower(Client.name) == body.name.lower()),
                )
            )
        ).scalar_one_or_none()
        if duplicate is not None:
            raise ClientExists(extra={"client_id": ids.encode(ids.CLIENT, duplicate)})
        slug = await _unique_slug(session, scope, _slug(body.name))
        client = Client(
            workspace_id=scope.workspace_id,
            name=body.name,
            slug=slug,
            website_url=website_url,
            website_domain=domain,
            own_channel_id=channel_id,
            own_channel_status="proposed" if channel_id else "unresolved",
            timezone=body.market.timezone,
            country=body.market.country,
            language=body.market.language,
            lifecycle=ClientLifecycle.created.value,
        )
        write_settings(
            client, merged(settings, channel_ref=body.channel_ref) if body.channel_ref else settings
        )
        session.add(client)
        await session.flush()
        await session.refresh(client)
    client_scope = scope.for_client(client.id)
    onboarding, unavailable = (None, None)
    if body.start_onboarding:
        onboarding, unavailable = await _start_onboarding(state, client_scope, _requested_by(context))
        async with state.container.session_factory() as session:
            client = await session.get(Client, client.id)
    out = ClientCreatedOut(
        client=await present_client(state, client),
        onboarding_run=onboarding,
        onboarding_unavailable=unavailable,
    )
    location = f"/api/v1/clients/{ids.encode(ids.CLIENT, client.id)}"
    return await guard.complete(json_response(out, 201, {"Location": location}))


async def _unique_slug(session, scope: TenantScope, base: str) -> str:
    taken = set(
        (
            await session.execute(
                select(Client.slug).where(
                    Client.workspace_id == scope.workspace_id, Client.slug.like(f"{base}%")
                )
            )
        )
        .scalars()
        .all()
    )
    if base not in taken:
        return base
    suffix = 2
    while f"{base}-{suffix}" in taken:
        suffix += 1
    return f"{base}-{suffix}"


def _requested_by(context: Access) -> str:
    return f"{context.principal.kind}:{context.principal.id}"


@router.get(
    "/clients",
    operation_id="clients_list",
    response_model=Page[ClientOut],
    dependencies=[Depends(only_params("lifecycle", "q", *PAGING_PARAMS))],
)
async def clients_list(
    state: State,
    scope: WorkspaceScope,
    lifecycle: ClientLifecycle | None = None,
    q: Annotated[str | None, Query(max_length=120)] = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> Page[ClientOut]:
    filters = {"lifecycle": lifecycle, "q": q}
    after = state.cursors.decode(cursor, filters)
    statement = select(Client).where(Client.workspace_id == scope.workspace_id, Client.deleted_at.is_(None))
    if lifecycle is not None:
        statement = statement.where(Client.lifecycle == lifecycle.value)
    if q:
        statement = statement.where(Client.name.ilike(f"%{q}%"))
    if after is not None:
        created_at, client_id = datetime.fromisoformat(after[0]), ids.decode(ids.CLIENT, after[1])
        statement = statement.where((Client.created_at, Client.id) < (created_at, client_id))
    statement = statement.order_by(Client.created_at.desc(), Client.id.desc()).limit(limit + 1)
    async with state.container.session_factory() as session:
        rows = list((await session.execute(statement)).scalars().all())
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        last = rows[-1]
        next_cursor = state.cursors.encode(
            [last.created_at.isoformat(), ids.encode(ids.CLIENT, last.id)], filters
        )
    return Page(items=await present_clients(state, rows), next_cursor=next_cursor, limit=limit)


@router.get("/clients/{client_id}", operation_id="clients_get", response_model=ClientOut)
async def clients_get(state: State, scope: ClientScope) -> JSONResponse:
    client = await _load(state, scope)
    out = await present_client(state, client)
    return json_response(out, headers={"ETag": f'"{out.version}"'})


async def _load(state: State, scope: TenantScope) -> Client:
    async with state.container.session_factory() as session:
        return (
            await session.execute(
                select(Client).where(Client.id == scope.client_id, Client.workspace_id == scope.workspace_id)
            )
        ).scalar_one()


@router.patch("/clients/{client_id}", operation_id="clients_update", response_model=ClientUpdateOut)
async def clients_update(
    body: ClientPatch,
    state: State,
    scope: ClientScope,
    context: Access,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> JSONResponse:
    async with state.container.session_factory() as session, session.begin():
        client = (
            await session.execute(
                select(Client).where(Client.id == scope.client_id).with_for_update(key_share=True)
            )
        ).scalar_one()
        settings = read_settings(client)
        if if_match is not None and if_match.strip('"W/') != str(settings.version):
            raise PreconditionFailed(
                f"the client is at version {settings.version}", extra={"version": settings.version}
            )
        changes: dict = {"version": settings.version + 1}
        window_changed = body.window_days is not None and body.window_days != settings.window_days
        if body.name is not None:
            client.name = body.name
        if window_changed:
            changes["window_days"] = body.window_days
        if body.market is not None:
            client.country, client.language, client.timezone = (
                body.market.country,
                body.market.language,
                body.market.timezone,
            )
        if window_changed or body.market is not None:
            changes["config_version"] = settings.config_version + 1
        if body.refresh_hour_local is not None:
            changes["refresh_hour_local"] = body.refresh_hour_local
        if body.paused is not None:
            changes.update(await _apply_pause(session, scope, client, settings, body.paused))
        write_settings(client, merged(settings, **changes))
        client.updated_at = func.now()
    client = await _load(state, scope)
    cards_run = None
    if window_changed and client.lifecycle == ClientLifecycle.active.value:
        cards_run = await _request_cards(state, scope, _requested_by(context))
    out = ClientUpdateOut(client=await present_client(state, client), cards_run=cards_run)
    return json_response(out, headers={"ETag": f'"{out.client.version}"'})


async def _apply_pause(
    session, scope: TenantScope, client: Client, settings: ClientSettings, paused: bool
) -> dict:
    current = ClientLifecycle(client.lifecycle)
    if paused and current is not ClientLifecycle.paused:
        _transition(current, ClientLifecycle.paused)
        client.lifecycle = ClientLifecycle.paused.value
        await ScheduleBook(session, scope).set_paused(True)
        return {"paused_from": current.value}
    if not paused and current is ClientLifecycle.paused:
        target = ClientLifecycle(settings.paused_from or ClientLifecycle.active.value)
        client.lifecycle = target.value
        await ScheduleBook(session, scope).set_paused(False)
        return {"paused_from": None}
    return {}


def _transition(current: ClientLifecycle, target: ClientLifecycle) -> None:
    try:
        ClientLifecycleMachine.ensure(current, target)
    except TransitionError as error:
        raise ConfigInvalidError(str(error)) from None


async def _request_cards(state: State, scope: TenantScope, requested_by: str) -> RunOut | None:
    try:
        result = await state.pipeline.runs.request_run(
            scope, RunRequest(kind=RunKind.cards, trigger=RunTrigger.user, requested_by=requested_by)
        )
    except PipelineUnavailable:
        return None
    return await present_run(state, result.run)


@router.delete(
    "/clients/{client_id}", operation_id="clients_delete", status_code=202, response_model=DeletingOut
)
async def clients_delete(state: State, scope: ClientScope) -> JSONResponse:
    async with state.container.session_factory() as session, session.begin():
        await session.execute(
            update(Client)
            .where(Client.id == scope.client_id)
            .values(lifecycle=ClientLifecycle.deleting.value, updated_at=func.now())
        )
    active = await state.pipeline.runs.active_run(scope)
    if active is not None:
        with contextlib.suppress(RunNotCancellable):
            await state.pipeline.runs.cancel(scope, active.id, reason="client_deleted")
    if state.job_queue is not None:
        # with redis down the reconciler enqueues the purge on its next tick
        with contextlib.suppress(RedisError, OSError):
            await state.job_queue.enqueue(
                PURGE_CLIENT,
                str(scope.client_id),
                job_id=f"purge:{scope.client_id}",
                queue=state.settings.workers.maintenance_queue,
            )
    return json_response(DeletingOut(), 202)


@router.post(
    "/clients/{client_id}/channel/resolve",
    operation_id="clients_channel_resolve",
    response_model=ChannelResolveOut,
)
async def clients_channel_resolve(
    body: ChannelResolveIn, state: State, scope: ClientScope
) -> ChannelResolveOut:
    return await state.service(CHANNELS).resolve(scope, body.channel_ref)


@router.post(
    "/clients/{client_id}/channel/attach",
    operation_id="clients_channel_attach",
    response_model=ClientOut,
)
async def clients_channel_attach(body: ChannelAttachIn, state: State, scope: ClientScope) -> JSONResponse:
    # the user's explicit pick from the ownership candidates; null continues website-only
    now = state.container.clock.now()
    async with state.container.session_factory() as session, session.begin():
        client = (
            await session.execute(
                select(Client).where(Client.id == scope.client_id).with_for_update(key_share=True)
            )
        ).scalar_one()
        settings = read_settings(client)
        if body.channel_id is None:
            client.own_channel_id, client.own_channel_status = None, "unresolved"
        else:
            client.own_channel_id, client.own_channel_status = body.channel_id, "verified"
            client.own_channel_verification = [
                {"verified_by": "manual", "channel_id": body.channel_id, "at": now.isoformat()},
                *(client.own_channel_verification or []),
            ]
        write_settings(
            client,
            merged(settings, version=settings.version + 1, config_version=settings.config_version + 1),
        )
        client.updated_at = func.now()
    out = await present_client(state, await _load(state, scope))
    return json_response(out, headers={"ETag": f'"{out.version}"'})


@router.post(
    "/clients/{client_id}/onboarding",
    operation_id="clients_onboarding_start",
    status_code=202,
    response_model=RunOut,
    dependencies=[Depends(run_start_limit)],
)
async def clients_onboarding_start(
    body: OnboardingIn,
    state: State,
    scope: ClientScope,
    context: Access,
    guard: Annotated[IdempotentRequest, Depends(idempotency(required_for_api_keys=False))],
) -> JSONResponse:
    if guard.replay is not None:
        return guard.replay
    channel_id = await _resolve_channel(state, scope, body.channel_ref)
    async with state.container.session_factory() as session, session.begin():
        client = (
            await session.execute(
                select(Client).where(Client.id == scope.client_id).with_for_update(key_share=True)
            )
        ).scalar_one()
        if body.website_url is not None:
            client.website_url = str(body.website_url)
            client.website_domain = _domain(client.website_url)
        if channel_id is not None and channel_id != client.own_channel_id:
            client.own_channel_id, client.own_channel_status = channel_id, "proposed"
        if body.channel_ref is not None:
            write_settings(client, merged(read_settings(client), channel_ref=body.channel_ref))
    result = await state.pipeline.runs.request_run(
        scope,
        RunRequest(kind=RunKind.onboarding, trigger=RunTrigger.user, requested_by=_requested_by(context)),
    )
    return await run_accepted(state, result, guard)
