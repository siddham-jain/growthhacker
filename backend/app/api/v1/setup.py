from typing import Annotated

from fastapi import APIRouter, Body, Depends, Header, Query, Response
from fastapi.responses import JSONResponse

from app.api.deps import Access, ClientScope, State, idempotency, run_start_limit
from app.api.errors import PROBLEM_RESPONSES, PreconditionRequired
from app.api.idempotency import IdempotentRequest
from app.api.pagination import Page, PageRequest
from app.api.presenters import present_run
from app.api.schemas.common import CountOut
from app.api.schemas.runs import RunOut
from app.api.schemas.setup import (
    BulkStatusIn,
    BusinessProfileView,
    BuyerQuestionCreate,
    BuyerQuestionItem,
    CompetitorAddIn,
    CompetitorRejectIn,
    CompetitorRolePatch,
    CompetitorView,
    ConfirmSetupIn,
    ConfirmSetupOut,
    KeywordCreate,
    KeywordItem,
    SetupCounts,
    TopicRefPatch,
    TopicRefStatus,
)
from app.api.services import COMPETITORS, PROFILE, TOPIC_REFS, TopicRefKind
from app.api.v1.common import PAGING_PARAMS, json_response, only_params, run_accepted
from app.domain.competitors import CandidateStatus
from app.domain.runs import RunKind, RunTrigger
from app.pipeline.runs import RunRequest

router = APIRouter(tags=["setup"], responses=PROBLEM_RESPONSES)


def _requested_by(context: Access) -> str:
    return f"{context.principal.kind}:{context.principal.id}"


def _version(if_match: str | None) -> int:
    if if_match is None:
        raise PreconditionRequired("send If-Match with the profile version you edited")
    value = if_match.strip().removeprefix("W/").strip('"')
    if not value.isdigit():
        raise PreconditionRequired("If-Match must carry the profile version")
    return int(value)


@router.get("/clients/{client_id}/profile", operation_id="profile_get", response_model=BusinessProfileView)
async def profile_get(state: State, scope: ClientScope) -> JSONResponse:
    view = await state.service(PROFILE).get(scope)
    return json_response(view, headers={"ETag": f'"{view.version}"'})


@router.patch(
    "/clients/{client_id}/profile",
    operation_id="profile_update",
    response_model=BusinessProfileView,
    openapi_extra={
        "requestBody": {"content": {"application/merge-patch+json": {"schema": {"type": "object"}}}}
    },
)
async def profile_update(
    state: State,
    scope: ClientScope,
    merge_patch: Annotated[dict, Body(media_type="application/merge-patch+json")],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> JSONResponse:
    view = await state.service(PROFILE).patch(scope, merge_patch, _version(if_match))
    return json_response(view, headers={"ETag": f'"{view.version}"'})


@router.post(
    "/clients/{client_id}/profile/recrawl",
    operation_id="profile_recrawl",
    status_code=202,
    response_model=RunOut,
    dependencies=[Depends(run_start_limit)],
)
async def profile_recrawl(state: State, scope: ClientScope, context: Access) -> JSONResponse:
    result = await state.pipeline.runs.request_run(
        scope, RunRequest(kind=RunKind.recrawl, trigger=RunTrigger.user, requested_by=_requested_by(context))
    )
    return await run_accepted(state, result)


def _topic_routes(path: str, kind: TopicRefKind, item: type, create: type, tag: str) -> None:
    @router.get(
        f"/clients/{{client_id}}/{path}",
        operation_id=f"{tag}_list",
        response_model=Page[item],
        dependencies=[Depends(only_params("status", *PAGING_PARAMS))],
    )
    async def list_items(
        state: State,
        scope: ClientScope,
        status: TopicRefStatus | None = None,
        cursor: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
    ):
        return await state.service(TOPIC_REFS).list_items(
            scope, kind, status, PageRequest(cursor=cursor, limit=limit)
        )

    @router.post(
        f"/clients/{{client_id}}/{path}", operation_id=f"{tag}_create", status_code=201, response_model=item
    )
    async def create_item(body: create, state: State, scope: ClientScope):  # type: ignore[valid-type]
        return await state.service(TOPIC_REFS).create(scope, kind, body)

    @router.patch(
        f"/clients/{{client_id}}/{path}/{{item_id}}", operation_id=f"{tag}_update", response_model=item
    )
    async def update_item(body: TopicRefPatch, item_id: str, state: State, scope: ClientScope):
        return await state.service(TOPIC_REFS).update(scope, kind, item_id, body)

    @router.delete(
        f"/clients/{{client_id}}/{path}/{{item_id}}", operation_id=f"{tag}_delete", status_code=204
    )
    async def delete_item(item_id: str, state: State, scope: ClientScope) -> Response:
        await state.service(TOPIC_REFS).delete(scope, kind, item_id)
        return Response(status_code=204)

    @router.post(
        f"/clients/{{client_id}}/{path}/bulk-status",
        operation_id=f"{tag}_bulk_status",
        response_model=CountOut,
    )
    async def bulk_status(body: BulkStatusIn, state: State, scope: ClientScope) -> CountOut:
        updated = await state.service(TOPIC_REFS).bulk_status(scope, kind, body.ids, body.status)
        return CountOut(updated=updated)


_topic_routes("keywords", "keyword", KeywordItem, KeywordCreate, "keywords")
_topic_routes("buyer-questions", "buyer_question", BuyerQuestionItem, BuyerQuestionCreate, "buyer_questions")


@router.get(
    "/clients/{client_id}/competitors",
    operation_id="competitors_list",
    response_model=Page[CompetitorView],
    dependencies=[Depends(only_params("status", *PAGING_PARAMS))],
)
async def competitors_list(
    state: State,
    scope: ClientScope,
    status: CandidateStatus | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> Page[CompetitorView]:
    return await state.service(COMPETITORS).list_competitors(
        scope, status, PageRequest(cursor=cursor, limit=limit)
    )


@router.post(
    "/clients/{client_id}/competitors",
    operation_id="competitors_add",
    status_code=201,
    response_model=CompetitorView,
)
async def competitors_add(
    body: CompetitorAddIn,
    state: State,
    scope: ClientScope,
    guard: Annotated[IdempotentRequest, Depends(idempotency(required_for_api_keys=False))],
) -> JSONResponse:
    if guard.replay is not None:
        return guard.replay
    view = await state.service(COMPETITORS).add(scope, body)
    return await guard.complete(json_response(view, 201))


@router.post(
    "/clients/{client_id}/competitors/suggest",
    operation_id="competitors_suggest",
    status_code=202,
    response_model=RunOut,
    dependencies=[Depends(run_start_limit)],
)
async def competitors_suggest(state: State, scope: ClientScope, context: Access) -> JSONResponse:
    result = await state.pipeline.runs.request_run(
        scope,
        RunRequest(
            kind=RunKind.onboarding,
            trigger=RunTrigger.user,
            pipeline="competitor_suggest",
            requested_by=_requested_by(context),
        ),
    )
    return await run_accepted(state, result)


@router.post(
    "/clients/{client_id}/competitors/{competitor_id}/confirm",
    operation_id="competitors_confirm",
    response_model=CompetitorView,
)
async def competitors_confirm(competitor_id: str, state: State, scope: ClientScope) -> CompetitorView:
    return await state.service(COMPETITORS).confirm(scope, competitor_id)


@router.post(
    "/clients/{client_id}/competitors/{competitor_id}/reject",
    operation_id="competitors_reject",
    response_model=CompetitorView,
)
async def competitors_reject(
    competitor_id: str, body: CompetitorRejectIn, state: State, scope: ClientScope
) -> CompetitorView:
    return await state.service(COMPETITORS).reject(scope, competitor_id, body)


@router.patch(
    "/clients/{client_id}/competitors/{competitor_id}",
    operation_id="competitors_set_role",
    response_model=CompetitorView,
)
async def competitors_set_role(
    competitor_id: str, body: CompetitorRolePatch, state: State, scope: ClientScope
) -> CompetitorView:
    # moves a competitor between the competitor, niche, benchmark and creator-seed sets
    return await state.service(COMPETITORS).set_role(scope, competitor_id, body)


@router.delete(
    "/clients/{client_id}/competitors/{competitor_id}", operation_id="competitors_delete", status_code=204
)
async def competitors_delete(competitor_id: str, state: State, scope: ClientScope) -> Response:
    await state.service(COMPETITORS).delete(scope, competitor_id)
    return Response(status_code=204)


@router.post(
    "/clients/{client_id}/confirm-setup",
    operation_id="clients_confirm_setup",
    status_code=202,
    response_model=ConfirmSetupOut,
    dependencies=[Depends(run_start_limit)],
)
async def clients_confirm_setup(
    body: ConfirmSetupIn,
    state: State,
    scope: ClientScope,
    context: Access,
    guard: Annotated[IdempotentRequest, Depends(idempotency(required_for_api_keys=True))],
) -> JSONResponse:
    if guard.replay is not None:
        return guard.replay
    result, validation = await state.pipeline.setup.confirm(
        scope, window_days=body.window_days, requested_by=_requested_by(context)
    )
    out = ConfirmSetupOut(
        run=await present_run(state, result.run, with_stages=True),
        validation=SetupCounts(
            competitors_confirmed=validation.competitors_confirmed,
            keywords_active=validation.keywords_active,
            buyer_questions_active=validation.buyer_questions_active,
        ),
    )
    return await guard.complete(json_response(out, 200 if result.deduplicated else 202))
