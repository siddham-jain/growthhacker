from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from sqlalchemy import select
from sse_starlette import EventSourceResponse, ServerSentEvent

from app.api import ids
from app.api.deps import ClientScope, State, authenticate, limited, spend_limit
from app.api.errors import PROBLEM_RESPONSES, InvalidCredentials, NotFound, Overloaded
from app.api.schemas.runs import EventsPage, RunEventOut
from app.api.signing import SignatureInvalid
from app.api.sse import client_event_stream, run_event_stream
from app.api.state import ApiState
from app.api.v1.runs import STREAM_TOKEN_PURPOSE
from app.core.tenancy import TenantScope
from app.domain.runs import RunStatus
from app.models.runs import PipelineRun
from app.pipeline.lifecycle import RunStateMachine
from app.pipeline.progress import EventLog, client_channel, run_channel

router = APIRouter(tags=["events"], responses=PROBLEM_RESPONSES)

SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


async def _run_scope(request: Request, state: ApiState, run_id: UUID, token: str | None) -> TenantScope:
    # EventSource cannot send headers, so third parties pass a short-lived token scoped to one run
    if token is None:
        context = await authenticate(request, state)
        if context.principal.workspace_id is None:
            raise NotFound("run not found")
        return context.principal.scope()
    try:
        claims = state.signer.verify(STREAM_TOKEN_PURPOSE, token)
    except SignatureInvalid:
        raise InvalidCredentials("the stream token is not valid") from None
    if (
        claims.get("run") != str(run_id)
        or int(claims.get("exp", 0)) < state.container.clock.now().timestamp()
    ):
        raise InvalidCredentials("the stream token is not valid for this run")
    return TenantScope(workspace_id=UUID(claims["ws"]))


async def _run_row(state: ApiState, scope: TenantScope, run_id: UUID) -> PipelineRun:
    async with state.container.session_factory() as session:
        run = (
            await session.execute(
                select(PipelineRun).where(
                    PipelineRun.id == run_id, PipelineRun.workspace_id == scope.workspace_id
                )
            )
        ).scalar_one_or_none()
    if run is None:
        raise NotFound("run not found")
    return run


def _last_id(last_event_id: str | None, after: int | None) -> int:
    if last_event_id is not None and last_event_id.strip().isdigit():
        return int(last_event_id.strip())
    return after or 0


@router.get(
    "/runs/{run_id}/events",
    operation_id="runs_events_stream",
    responses={200: {"content": {"text/event-stream": {}}}, 204: {"description": "the run already finished"}},
)
async def runs_events_stream(
    request: Request,
    run_id: str,
    state: State,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    after_seq: Annotated[int | None, Query(ge=0)] = None,
    token: str | None = None,
) -> Response:
    identifier = ids.decode(ids.RUN, run_id)
    scope = await _run_scope(request, state, identifier, token)
    await spend_limit(request, state, f"run:{identifier}" if token else _subject(request), "sse_open")
    run = await _run_row(state, scope, identifier)
    last = _last_id(last_event_id, after_seq)
    if RunStateMachine.is_terminal(RunStatus(run.status)) and last >= run.event_seq:
        # 204 tells EventSource to stop reconnecting
        return Response(status_code=204)
    if state.hub.at_capacity():
        raise Overloaded("too many live streams on this server; poll events.json instead", retry_after_s=5)
    log = EventLog(state.container.session_factory, scope)
    subscription = state.hub.register(run_channel(state.settings.redis.key_prefix, identifier))

    async def status() -> RunStatus:
        return RunStatus((await _run_row(state, scope, identifier)).status)

    async def stream() -> AsyncIterator[ServerSentEvent]:
        try:
            async for event in run_event_stream(
                run_id=identifier,
                last_seq=last,
                log=log,
                subscription=subscription,
                hub=state.hub,
                run_status=status,
                poll_s=state.settings.api.sse_poll_s,
                shutdown=state.shutdown,
            ):
                yield event
        finally:
            state.hub.unregister(subscription)

    return _event_source(state, stream())


@router.get("/runs/{run_id}/events.json", operation_id="runs_events_poll", response_model=EventsPage)
async def runs_events_poll(
    request: Request,
    run_id: str,
    state: State,
    after_seq: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    token: str | None = None,
) -> EventsPage:
    identifier = ids.decode(ids.RUN, run_id)
    scope = await _run_scope(request, state, identifier, token)
    await spend_limit(request, state, f"run:{identifier}" if token else _subject(request), "read")
    run = await _run_row(state, scope, identifier)
    events = await EventLog(state.container.session_factory, scope).after(identifier, after_seq, limit=limit)
    return EventsPage(
        items=[RunEventOut.from_event(stored.event) for stored in events],
        last_seq=events[-1].event.seq if events else after_seq,
        run_status=RunStatus(run.status),
    )


@router.get(
    "/clients/{client_id}/events",
    operation_id="clients_events_stream",
    responses={200: {"content": {"text/event-stream": {}}}},
    dependencies=[Depends(limited("sse_open"))],
)
async def clients_events_stream(
    state: State,
    scope: ClientScope,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> Response:
    if state.hub.at_capacity():
        raise Overloaded("too many live streams on this server", retry_after_s=5)
    assert scope.client_id is not None
    client_id = scope.client_id
    log = EventLog(state.container.session_factory, scope)
    subscription = state.hub.register(client_channel(state.settings.redis.key_prefix, client_id))

    async def stream() -> AsyncIterator[ServerSentEvent]:
        try:
            async for event in client_event_stream(
                client_id=client_id,
                last_id=_last_id(last_event_id, None),
                log=log,
                subscription=subscription,
                poll_s=state.settings.api.sse_poll_s,
                shutdown=state.shutdown,
            ):
                yield event
        finally:
            state.hub.unregister(subscription)

    return _event_source(state, stream())


def _subject(request: Request) -> str:
    context = request.state.auth
    return f"{context.principal.kind}:{context.principal.id}"


def _event_source(state: ApiState, content: AsyncIterator[ServerSentEvent]) -> EventSourceResponse:
    api = state.settings.api
    return EventSourceResponse(
        content,
        ping=int(api.sse_ping_s),
        send_timeout=api.sse_send_timeout_s,
        headers=SSE_HEADERS,
        shutdown_event=state.shutdown,
        shutdown_grace_period=3,
    )
