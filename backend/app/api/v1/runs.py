from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.api import ids
from app.api.deps import Access, ClientScope, State, WorkspaceScope, idempotency, run_start_limit
from app.api.errors import PROBLEM_RESPONSES, BadRequest
from app.api.idempotency import IdempotentRequest
from app.api.pagination import Page
from app.api.presenters import present_run
from app.api.schemas.runs import RunCancelIn, RunCreate, RunOut, RunRetryIn, StreamTokenOut
from app.api.v1.common import PAGING_PARAMS, json_response, only_params, run_accepted
from app.domain.runs import RunKind, RunStatus, RunTrigger
from app.models.runs import PipelineRun
from app.pipeline.runs import RunRequest

router = APIRouter(tags=["runs"], responses=PROBLEM_RESPONSES)

STREAM_TOKEN_PURPOSE = "run_stream"


def _requested_by(context: Access) -> str:
    return f"{context.principal.kind}:{context.principal.id}"


@router.post(
    "/clients/{client_id}/runs",
    operation_id="runs_create",
    status_code=202,
    response_model=RunOut,
    responses={200: {"model": RunOut, "description": "joined an equivalent active run"}},
    dependencies=[Depends(run_start_limit)],
)
async def runs_create(
    body: RunCreate,
    state: State,
    scope: ClientScope,
    context: Access,
    guard: Annotated[IdempotentRequest, Depends(idempotency(required_for_api_keys=True))],
) -> JSONResponse:
    if guard.replay is not None:
        return guard.replay
    if body.from_stage is not None and body.resume_of is None:
        raise BadRequest("from_stage is only valid together with resume_of")
    result = await state.pipeline.runs.request_run(
        scope,
        RunRequest(
            kind=RunKind(body.kind),
            trigger=RunTrigger.resume if body.resume_of else RunTrigger.user,
            window_days=body.window_days,
            force=body.force,
            from_stage=body.from_stage,
            resume_of=None if body.resume_of is None else ids.decode(ids.RUN, body.resume_of),
            idempotency_key=guard.key,
            requested_by=_requested_by(context),
        ),
    )
    return await run_accepted(state, result, guard)


@router.get(
    "/clients/{client_id}/runs",
    operation_id="runs_list",
    response_model=Page[RunOut],
    dependencies=[Depends(only_params("kind", "status", *PAGING_PARAMS))],
)
async def runs_list(
    state: State,
    scope: ClientScope,
    kind: RunKind | None = None,
    status: RunStatus | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> Page[RunOut]:
    filters = {"kind": kind, "status": status, "client": str(scope.client_id)}
    after = state.cursors.decode(cursor, filters)
    statement = select(PipelineRun).where(
        PipelineRun.workspace_id == scope.workspace_id, PipelineRun.client_id == scope.client_id
    )
    if kind is not None:
        statement = statement.where(PipelineRun.kind == kind.value)
    if status is not None:
        statement = statement.where(PipelineRun.status == status.value)
    if after is not None:
        statement = statement.where(
            (PipelineRun.created_at, PipelineRun.id)
            < (datetime.fromisoformat(after[0]), ids.decode(ids.RUN, after[1]))
        )
    statement = statement.order_by(PipelineRun.created_at.desc(), PipelineRun.id.desc()).limit(limit + 1)
    async with state.container.session_factory() as session:
        rows = list((await session.execute(statement)).scalars().all())
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = state.cursors.encode(
            [rows[-1].created_at.isoformat(), ids.encode(ids.RUN, rows[-1].id)], filters
        )
    return Page(items=[await present_run(state, run) for run in rows], next_cursor=next_cursor, limit=limit)


@router.get("/runs/{run_id}", operation_id="runs_get", response_model=RunOut)
async def runs_get(run_id: str, state: State, scope: WorkspaceScope) -> RunOut:
    run = await state.pipeline.runs.get(scope, ids.decode(ids.RUN, run_id))
    return await present_run(state, run, with_stages=True)


@router.post("/runs/{run_id}/cancel", operation_id="runs_cancel", status_code=202, response_model=RunOut)
async def runs_cancel(
    run_id: str, state: State, scope: WorkspaceScope, body: RunCancelIn | None = None
) -> JSONResponse:
    run = await state.pipeline.runs.cancel(
        scope, ids.decode(ids.RUN, run_id), None if body is None else body.reason
    )
    return json_response(await present_run(state, run, with_stages=True), 202)


@router.post(
    "/runs/{run_id}/retry",
    operation_id="runs_retry",
    status_code=202,
    response_model=RunOut,
    dependencies=[Depends(run_start_limit)],
)
async def runs_retry(
    run_id: str, state: State, scope: WorkspaceScope, body: RunRetryIn | None = None
) -> JSONResponse:
    result = await state.pipeline.runs.retry(
        scope, ids.decode(ids.RUN, run_id), None if body is None else body.from_stage
    )
    return await run_accepted(state, result)


@router.post("/runs/{run_id}/stream-token", operation_id="runs_stream_token", response_model=StreamTokenOut)
async def runs_stream_token(run_id: str, state: State, scope: WorkspaceScope) -> StreamTokenOut:
    run = await state.pipeline.runs.get(scope, ids.decode(ids.RUN, run_id))
    expires_at = state.container.clock.now() + timedelta(seconds=state.settings.api.stream_token_ttl_s)
    token = state.signer.sign(
        STREAM_TOKEN_PURPOSE,
        {"run": str(run.id), "ws": str(run.workspace_id), "exp": int(expires_at.timestamp())},
    )
    return StreamTokenOut(token=token, expires_at=expires_at)
