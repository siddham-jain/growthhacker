import json
import secrets
from datetime import date, datetime
from typing import Annotated
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import JSONResponse, RedirectResponse

from app.api import ids
from app.api.deps import Access, ClientScope, Owner, State, WorkspaceScope, limited, require_role
from app.api.errors import PROBLEM_RESPONSES, AppError, NotFound, OAuthStateInvalid
from app.api.pagination import Page, PageRequest
from app.api.schemas.admin import (
    BacktestCreate,
    LabelIn,
    LabelOut,
    OpsQuotaOut,
    OwnerReport,
    PrecisionOut,
    QueuesOut,
    QueueView,
    ReviewIn,
)
from app.api.schemas.runs import RunOut
from app.api.services import EVALUATION, OPS, YOUTUBE_OAUTH
from app.api.state import ApiState
from app.api.v1.common import PAGING_PARAMS, only_params, run_accepted
from app.core.tenancy import Role, TenantScope
from app.domain.runs import RunKind, RunTrigger
from app.models.tenancy import Client
from app.pipeline.runs import RunRequest

router = APIRouter(responses=PROBLEM_RESPONSES)
# google redirects the browser here; the single-use state is the credential
public_router = APIRouter(responses=PROBLEM_RESPONSES)

Editor = Depends(require_role(Role.editor))


def _state_key(state: ApiState, token: str) -> str:
    return f"{state.settings.redis.key_prefix}:oauth_state:{token}"


async def _client_in_workspace(state: ApiState, scope: TenantScope, client_id: str) -> TenantScope:
    identifier = ids.decode(ids.CLIENT, client_id)
    async with state.container.session_factory() as session:
        client = await session.get(Client, identifier)
    if client is None or client.workspace_id != scope.workspace_id or client.deleted_at is not None:
        raise NotFound("client not found")
    return scope.for_client(identifier)


@router.get(
    "/integrations/youtube/connect",
    tags=["integrations"],
    operation_id="integrations_youtube_connect",
    dependencies=[Editor, Depends(limited("oauth"))],
)
async def integrations_youtube_connect(
    state: State,
    scope: WorkspaceScope,
    context: Access,
    client_id: str,
    redirect: Annotated[int, Query(ge=0, le=1)] = 0,
) -> Response:
    service = state.service(YOUTUBE_OAUTH)
    client_scope = await _client_in_workspace(state, scope, client_id)
    token = secrets.token_urlsafe(32)
    # single use, ten minutes: the callback consumes it with GETDEL
    await state.container.redis.set(
        _state_key(state, token),
        json.dumps(
            {
                "workspace_id": str(client_scope.workspace_id),
                "client_id": str(client_scope.client_id),
                "user_id": None if context.user_id is None else str(context.user_id),
            }
        ),
        ex=state.settings.api.oauth_state_ttl_s,
    )
    url = service.authorization_url(client_scope, token)
    if redirect:
        return RedirectResponse(url, status_code=302)
    return JSONResponse({"authorization_url": url})


@public_router.get(
    "/integrations/youtube/callback", tags=["integrations"], operation_id="integrations_youtube_callback"
)
async def integrations_youtube_callback(
    state: State, code: str, state_token: Annotated[str, Query(alias="state")]
) -> Response:
    service = state.service(YOUTUBE_OAUTH)
    raw = await state.container.redis.getdel(_state_key(state, state_token))
    if raw is None:
        raise OAuthStateInvalid("the connection link expired or was already used")
    claims = json.loads(raw)
    scope = TenantScope(workspace_id=UUID(claims["workspace_id"]), client_id=UUID(claims["client_id"]))
    user_id = None if claims.get("user_id") is None else UUID(claims["user_id"])
    target = f"{state.settings.api.frontend_url}/clients/{ids.encode(ids.CLIENT, scope.client_id)}"
    try:
        await service.complete(scope, code, user_id)
    except AppError as error:
        return RedirectResponse(
            f"{target}?{urlencode({'oauth': 'error', 'reason': error.code})}", status_code=302
        )
    return RedirectResponse(f"{target}?oauth=connected", status_code=302)


@router.delete(
    "/clients/{client_id}/integrations/youtube",
    tags=["integrations"],
    operation_id="integrations_youtube_disconnect",
    status_code=204,
)
async def integrations_youtube_disconnect(state: State, scope: ClientScope) -> Response:
    await state.service(YOUTUBE_OAUTH).disconnect(scope)
    return Response(status_code=204)


@router.post(
    "/evaluation/backtests",
    tags=["evaluation"],
    operation_id="evaluation_backtest_create",
    status_code=202,
    response_model=RunOut,
)
async def evaluation_backtest_create(
    body: BacktestCreate, state: State, scope: WorkspaceScope, context: Owner
) -> Response:
    client_scope = await _client_in_workspace(state, scope, body.client_id)
    result = await state.pipeline.runs.request_run(
        client_scope,
        RunRequest(
            kind=RunKind.backtest,
            trigger=RunTrigger.evaluation,
            window_days=body.window_days,
            as_of=body.frozen_at,
            requested_by=f"{context.principal.kind}:{context.principal.id}",
        ),
    )
    return await run_accepted(state, result)


@router.get(
    "/evaluation/backtests/{run_id}",
    tags=["evaluation"],
    operation_id="evaluation_backtest_get",
    response_model=OwnerReport,
)
async def evaluation_backtest_get(run_id: str, state: State, scope: WorkspaceScope) -> OwnerReport:
    return await state.service(EVALUATION).backtest_report(scope, ids.decode(ids.RUN, run_id))


@router.get(
    "/evaluation/outlier-labels",
    tags=["evaluation"],
    operation_id="evaluation_labels_list",
    response_model=Page[LabelOut],
    dependencies=[Depends(only_params("client_id", *PAGING_PARAMS))],
)
async def evaluation_labels_list(
    state: State,
    scope: WorkspaceScope,
    client_id: str | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> Page[LabelOut]:
    service = state.service(EVALUATION)
    client = None if client_id is None else (await _client_in_workspace(state, scope, client_id)).client_id
    return await service.labels(scope, client, PageRequest(cursor=cursor, limit=limit))


@router.put(
    "/evaluation/outlier-labels/{video_id}",
    tags=["evaluation"],
    operation_id="evaluation_label_put",
    response_model=LabelOut,
)
async def evaluation_label_put(video_id: str, body: LabelIn, state: State, scope: WorkspaceScope) -> LabelOut:
    return await state.service(EVALUATION).put_label(scope, video_id, body)


@router.get(
    "/evaluation/outlier-precision",
    tags=["evaluation"],
    operation_id="evaluation_outlier_precision",
    response_model=PrecisionOut,
    dependencies=[Depends(only_params("client_id"))],
)
async def evaluation_outlier_precision(
    state: State, scope: WorkspaceScope, client_id: str | None = None
) -> PrecisionOut:
    service = state.service(EVALUATION)
    client = None if client_id is None else (await _client_in_workspace(state, scope, client_id)).client_id
    return await service.precision(scope, client)


@router.get(
    "/evaluation/reviews/queue",
    tags=["evaluation"],
    operation_id="evaluation_review_queue",
    response_model=OwnerReport,
)
async def evaluation_review_queue(state: State, scope: WorkspaceScope) -> OwnerReport:
    return await state.service(EVALUATION).review_queue(scope)


@router.post(
    "/evaluation/reviews",
    tags=["evaluation"],
    operation_id="evaluation_review_submit",
    response_model=OwnerReport,
)
async def evaluation_review_submit(body: ReviewIn, state: State, scope: WorkspaceScope) -> OwnerReport:
    return await state.service(EVALUATION).submit_review(scope, body)


@router.get(
    "/evaluation/reviews/summary",
    tags=["evaluation"],
    operation_id="evaluation_review_summary",
    response_model=OwnerReport,
)
async def evaluation_review_summary(state: State, scope: WorkspaceScope) -> OwnerReport:
    return await state.service(EVALUATION).review_summary(scope)


async def _queues(state: ApiState) -> list[QueueView]:
    if state.job_queue is None:
        return []
    workers = state.settings.workers
    views = []
    for name in (workers.interactive_queue, workers.batch_queue, workers.maintenance_queue):
        stats = await state.job_queue.stats(name)
        views.append(
            QueueView(
                name=stats.name,
                depth=stats.depth,
                oldest_age_s=stats.oldest_age_s,
                worker_healthy=stats.worker_healthy,
            )
        )
    return views


@router.get(
    "/ops/quota",
    tags=["ops"],
    operation_id="ops_quota",
    response_model=OpsQuotaOut,
    dependencies=[Depends(only_params("date"))],
)
async def ops_quota(
    state: State,
    scope: WorkspaceScope,
    context: Owner,
    day: Annotated[date | None, Query(alias="date")] = None,
) -> OpsQuotaOut:
    quota = await state.service(OPS).quota(scope, day)
    return quota.model_copy(update={"queues": await _queues(state)})


@router.get(
    "/ops/report",
    tags=["ops"],
    operation_id="ops_report",
    response_model=OwnerReport,
    dependencies=[Depends(only_params("from", "to"))],
)
async def ops_report(
    state: State,
    scope: WorkspaceScope,
    context: Owner,
    start: Annotated[datetime | None, Query(alias="from")] = None,
    end: Annotated[datetime | None, Query(alias="to")] = None,
) -> OwnerReport:
    return await state.service(OPS).report(scope, start, end)


@router.get("/ops/queues", tags=["ops"], operation_id="ops_queues", response_model=QueuesOut)
async def ops_queues(state: State, context: Owner) -> QueuesOut:
    return QueuesOut(queues=await _queues(state))
