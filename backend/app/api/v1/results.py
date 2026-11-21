import hashlib
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Query, Response
from pydantic import BaseModel

from app.api.deps import Access, ClientScope, State, WorkspaceScope
from app.api.envelopes import ActiveResult, active_result, envelope
from app.api.errors import PROBLEM_RESPONSES, Forbidden
from app.api.pagination import Page, PageRequest
from app.api.presenters import present_run
from app.api.schemas.common import QueryWindowDays, ResultEnvelope
from app.api.schemas.results import (
    CompetitorDetail,
    CompetitorSheet,
    CreatorsData,
    EarlySignalView,
    FeedbackIn,
    FeedbackOut,
    GapMatrixData,
    HygieneData,
    ListingFormat,
    NewsOpportunityView,
    OutlierRow,
    OwnPerformanceData,
    RecommendationOut,
    RecommendationsData,
    RefreshAppliedIn,
    RefreshSuggestionView,
    TopicSummary,
    TrendingData,
    VideoRow,
)
from app.api.schemas.runs import RunOut
from app.api.services import (
    ANALYTICS,
    CREATORS,
    NEWS,
    RECOMMENDATIONS,
    REFRESH,
    TRENDS,
    AnalyticsScope,
    RecommendationFilters,
)
from app.api.v1.common import PAGING_PARAMS, json_response, only_params
from app.core.tenancy import TenantScope
from app.domain.analytics import ChannelLandscape, TraitLift
from app.domain.news import NewsSignal
from app.domain.recommendations import ASSIGNMENT_REC_TYPES, RecType
from app.domain.runs import RunKind, RunTrigger
from app.domain.trends import TrendScan
from app.models.tenancy import Client
from app.pipeline.client_settings import read_settings
from app.pipeline.runs import RunRequest

router = APIRouter(tags=["results"], responses=PROBLEM_RESPONSES)

Window = Annotated[QueryWindowDays | None, Query()]
Cursor = Annotated[str | None, Query()]
Limit = Annotated[int, Query(ge=1, le=100)]
CreatorSortParam = Literal[
    "matching_uploads", "median_outlier_ratio", "median_views_at_age", "last_upload_at", "uploads_90d"
]


class CardsPending(BaseModel):
    status: Literal["writing"] = "writing"
    window_days: int
    cards_run: RunOut | None


async def _published(state: State, scope: TenantScope, window_days: int | None) -> ActiveResult:
    if window_days is None:
        async with state.container.session_factory() as session:
            client = await session.get(Client, scope.client_id)
        window_days = read_settings(client).window_days if client is not None else 30
    return await active_result(state.container.session_factory, scope, window_days)


def listing_format(format: Literal["contract", "full"]) -> ListingFormat:
    if format == "full":
        return ListingFormat(format="full", types=list(RecType))
    excluded = sorted(set(RecType) - ASSIGNMENT_REC_TYPES)
    return ListingFormat(
        format="contract",
        types=sorted(ASSIGNMENT_REC_TYPES),
        excluded_types=excluded,
        note=(
            f"Strict contract mode lists only the assignment's card types; {', '.join(excluded)} "
            "cards are left out here and stay available with format=full."
        ),
    )


def _wrap[T](state: State, data: T, active: ActiveResult) -> ResultEnvelope[T]:
    return envelope(data, active, state.settings, state.container.clock.now())


@router.get(
    "/clients/{client_id}/analytics/own-performance",
    operation_id="analytics_own_performance",
    response_model=ResultEnvelope[OwnPerformanceData],
    dependencies=[Depends(only_params("window_days"))],
)
async def analytics_own_performance(
    state: State, scope: ClientScope, window_days: Window = None
) -> ResultEnvelope[OwnPerformanceData]:
    service = state.service(ANALYTICS)
    active = await _published(state, scope, window_days)
    return _wrap(state, await service.own_performance(scope, active.run_id, active.window_days), active)


@router.get(
    "/clients/{client_id}/analytics/competitors",
    operation_id="analytics_competitors",
    response_model=ResultEnvelope[list[CompetitorSheet]],
    dependencies=[Depends(only_params("window_days", "sort"))],
)
async def analytics_competitors(
    state: State, scope: ClientScope, window_days: Window = None, sort: str | None = None
) -> ResultEnvelope[list[CompetitorSheet]]:
    service = state.service(ANALYTICS)
    active = await _published(state, scope, window_days)
    return _wrap(state, await service.competitors(scope, active.run_id, active.window_days, sort), active)


@router.get(
    "/clients/{client_id}/analytics/competitors/{channel_id}",
    operation_id="analytics_competitor",
    response_model=ResultEnvelope[CompetitorDetail],
    dependencies=[Depends(only_params("window_days"))],
)
async def analytics_competitor(
    channel_id: str, state: State, scope: ClientScope, window_days: Window = None
) -> ResultEnvelope[CompetitorDetail]:
    service = state.service(ANALYTICS)
    active = await _published(state, scope, window_days)
    return _wrap(
        state, await service.competitor(scope, active.run_id, active.window_days, channel_id), active
    )


@router.get(
    "/clients/{client_id}/analytics/competitors/{channel_id}/videos",
    operation_id="analytics_competitor_videos",
    response_model=ResultEnvelope[Page[VideoRow]],
    dependencies=[Depends(only_params("window_days", "sort", "topic_ref_id", *PAGING_PARAMS))],
)
async def analytics_competitor_videos(
    channel_id: str,
    state: State,
    scope: ClientScope,
    window_days: Window = None,
    sort: str = "-outlier_ratio",
    topic_ref_id: str | None = None,
    cursor: Cursor = None,
    limit: Limit = 25,
) -> ResultEnvelope[Page[VideoRow]]:
    service = state.service(ANALYTICS)
    active = await _published(state, scope, window_days)
    page = await service.competitor_videos(
        scope,
        active.run_id,
        active.window_days,
        channel_id,
        sort,
        topic_ref_id,
        PageRequest(cursor=cursor, limit=limit),
    )
    return _wrap(state, page, active)


@router.get(
    "/clients/{client_id}/analytics/trending",
    operation_id="analytics_trending",
    response_model=ResultEnvelope[TrendingData],
    dependencies=[Depends(only_params("window_days"))],
)
async def analytics_trending(
    state: State, scope: ClientScope, window_days: Window = None
) -> ResultEnvelope[TrendingData]:
    service = state.service(ANALYTICS)
    active = await _published(state, scope, window_days)
    return _wrap(state, await service.trending(scope, active.run_id, active.window_days), active)


@router.get(
    "/clients/{client_id}/analytics/outliers",
    operation_id="analytics_outliers",
    response_model=ResultEnvelope[Page[OutlierRow]],
    dependencies=[Depends(only_params("scope", "window_days", "min_ratio", *PAGING_PARAMS))],
)
async def analytics_outliers(
    state: State,
    scope: ClientScope,
    analytics_scope: Annotated[AnalyticsScope, Query(alias="scope")] = "competitors",
    window_days: Window = None,
    min_ratio: Annotated[float | None, Query(ge=0)] = None,
    cursor: Cursor = None,
    limit: Limit = 25,
) -> ResultEnvelope[Page[OutlierRow]]:
    service = state.service(ANALYTICS)
    active = await _published(state, scope, window_days)
    page = await service.outliers(
        scope,
        active.run_id,
        active.window_days,
        analytics_scope,
        min_ratio,
        PageRequest(cursor=cursor, limit=limit),
    )
    return _wrap(state, page, active)


@router.get(
    "/clients/{client_id}/analytics/patterns",
    operation_id="analytics_patterns",
    response_model=ResultEnvelope[list[TraitLift]],
    dependencies=[Depends(only_params("scope", "window_days"))],
)
async def analytics_patterns(
    state: State,
    scope: ClientScope,
    analytics_scope: Annotated[Literal["competitors", "niche"], Query(alias="scope")] = "competitors",
    window_days: Window = None,
) -> ResultEnvelope[list[TraitLift]]:
    service = state.service(ANALYTICS)
    active = await _published(state, scope, window_days)
    return _wrap(
        state, await service.patterns(scope, active.run_id, active.window_days, analytics_scope), active
    )


@router.get(
    "/clients/{client_id}/analytics/topics",
    operation_id="analytics_topics",
    response_model=ResultEnvelope[list[TopicSummary]],
    dependencies=[Depends(only_params("window_days"))],
)
async def analytics_topics(
    state: State, scope: ClientScope, window_days: Window = None
) -> ResultEnvelope[list[TopicSummary]]:
    service = state.service(ANALYTICS)
    active = await _published(state, scope, window_days)
    return _wrap(state, await service.topics(scope, active.run_id, active.window_days), active)


@router.get(
    "/clients/{client_id}/analytics/gaps",
    operation_id="analytics_gaps",
    response_model=ResultEnvelope[GapMatrixData],
    dependencies=[Depends(only_params("window_days"))],
)
async def analytics_gaps(
    state: State, scope: ClientScope, window_days: Window = None
) -> ResultEnvelope[GapMatrixData]:
    service = state.service(ANALYTICS)
    active = await _published(state, scope, window_days)
    return _wrap(state, await service.gaps(scope, active.run_id, active.window_days), active)


@router.get(
    "/clients/{client_id}/analytics/landscape",
    operation_id="analytics_landscape",
    response_model=ResultEnvelope[ChannelLandscape],
    dependencies=[Depends(only_params())],
)
async def analytics_landscape(state: State, scope: ClientScope) -> ResultEnvelope[ChannelLandscape]:
    # fixed 90-day window and 365-day growth, independent of the analysis window
    service = state.service(ANALYTICS)
    active = await _published(state, scope, None)
    return _wrap(state, await service.landscape(scope, active.run_id), active)


@router.get(
    "/clients/{client_id}/trends",
    operation_id="trends_latest",
    response_model=ResultEnvelope[TrendScan],
    dependencies=[Depends(only_params())],
)
async def trends_latest(state: State, scope: ClientScope) -> ResultEnvelope[TrendScan]:
    active = await _published(state, scope, None)
    return _wrap(state, await state.service(TRENDS).latest(scope, active.window_days), active)


@router.get(
    "/clients/{client_id}/recommendations",
    operation_id="recommendations_list",
    response_model=ResultEnvelope[RecommendationsData],
    responses={
        202: {"model": CardsPending, "description": "cards for this window are being written"},
        304: {},
    },
    dependencies=[
        Depends(
            only_params(
                "type",
                "lens",
                "window_days",
                "buyer_question_id",
                "confidence",
                "status",
                "format",
                *PAGING_PARAMS,
            )
        )
    ],
)
async def recommendations_list(
    state: State,
    scope: ClientScope,
    context: Access,
    type: str | None = None,
    lens: Literal["views", "conversion"] | None = None,
    window_days: Window = None,
    buyer_question_id: str | None = None,
    confidence: Literal["High", "Medium", "Low"] | None = None,
    status: Literal["saved", "dismissed", "done", "snoozed"] | None = None,
    format: Literal["contract", "full"] = "full",
    cursor: Cursor = None,
    limit: Limit = 25,
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
) -> Response:
    service = state.service(RECOMMENDATIONS)
    active = await _published(state, scope, window_days)
    if not await service.cards_written(scope, active.run_id, active.window_days):
        result = await state.pipeline.runs.request_run(
            scope,
            RunRequest(
                kind=RunKind.cards,
                trigger=RunTrigger.user,
                window_days=active.window_days,
                requested_by=f"{context.principal.kind}:{context.principal.id}",
            ),
        )
        pending = CardsPending(window_days=active.window_days, cards_run=await present_run(state, result.run))
        return json_response(pending, 202)
    filters = RecommendationFilters(
        type=type,
        lens=lens,
        buyer_question_id=buyer_question_id,
        confidence=confidence,
        status=status,
        assignment_types_only=format == "contract",
    )
    etag = (
        '"'
        + hashlib.sha256(f"{active.run_id}|{filters}|{format}|{cursor}|{limit}".encode()).hexdigest()[:24]
        + '"'
    )
    if if_none_match == etag:
        return Response(status_code=304, headers={"ETag": etag})
    listing = await service.list_cards(
        scope,
        active.run_id,
        active.window_days,
        filters,
        PageRequest(cursor=cursor, limit=limit),
        context.user_id,
    )
    items = (
        [item.recommendation.strict_contract() for item in listing.items]
        if format == "contract"
        else listing.items
    )
    data = RecommendationsData(
        items=items,
        meta=listing_format(format),
        next_cursor=listing.next_cursor,
        limit=limit,
        notices=listing.notices,
        white_space=listing.white_space,
        suppressed_counts=listing.suppressed_counts,
    )
    return json_response(_wrap(state, data, active), headers={"ETag": etag})


@router.get(
    "/recommendations/{recommendation_id}",
    operation_id="recommendations_get",
    response_model=RecommendationOut,
)
async def recommendations_get(
    recommendation_id: str, state: State, scope: WorkspaceScope, context: Access
) -> RecommendationOut:
    lookup = await state.service(RECOMMENDATIONS).get(scope, recommendation_id, context.user_id)
    return lookup.item


@router.put(
    "/recommendations/{recommendation_id}/feedback",
    operation_id="recommendations_feedback",
    response_model=FeedbackOut,
)
async def recommendations_feedback(
    recommendation_id: str, body: FeedbackIn, state: State, scope: WorkspaceScope, context: Access
) -> FeedbackOut:
    service = state.service(RECOMMENDATIONS)
    if context.user_id is None:
        raise Forbidden("feedback is recorded per user; use a key created by a user")
    return FeedbackOut(feedback=await service.put_feedback(scope, recommendation_id, context.user_id, body))


@router.get(
    "/clients/{client_id}/news/opportunities",
    operation_id="news_opportunities",
    response_model=ResultEnvelope[Page[NewsOpportunityView]],
    dependencies=[Depends(only_params("status", *PAGING_PARAMS))],
)
async def news_opportunities(
    state: State,
    scope: ClientScope,
    status: Literal["new", "dismissed", "used"] | None = None,
    cursor: Cursor = None,
    limit: Limit = 25,
) -> ResultEnvelope[Page[NewsOpportunityView]]:
    service = state.service(NEWS)
    active = await _published(state, scope, None)
    page = await service.opportunities(scope, active.run_id, status, PageRequest(cursor=cursor, limit=limit))
    return _wrap(state, page, active)


@router.get(
    "/clients/{client_id}/news/signals",
    operation_id="news_signals",
    response_model=Page[NewsSignal],
    dependencies=[Depends(only_params(*PAGING_PARAMS))],
)
async def news_signals(
    state: State, scope: ClientScope, cursor: Cursor = None, limit: Limit = 25
) -> Page[NewsSignal]:
    return await state.service(NEWS).signals(scope, PageRequest(cursor=cursor, limit=limit))


@router.post(
    "/news/opportunities/{opportunity_id}/dismiss",
    operation_id="news_dismiss",
    response_model=NewsOpportunityView,
)
async def news_dismiss(opportunity_id: str, state: State, scope: WorkspaceScope) -> NewsOpportunityView:
    return await state.service(NEWS).dismiss(scope, opportunity_id)


@router.get(
    "/clients/{client_id}/refresh-suggestions",
    operation_id="refresh_list",
    response_model=ResultEnvelope[Page[RefreshSuggestionView]],
    dependencies=[Depends(only_params("status", "video_id", *PAGING_PARAMS))],
)
async def refresh_list(
    state: State,
    scope: ClientScope,
    status: Literal["open", "applied", "dismissed"] | None = None,
    video_id: str | None = None,
    cursor: Cursor = None,
    limit: Limit = 25,
) -> ResultEnvelope[Page[RefreshSuggestionView]]:
    service = state.service(REFRESH)
    active = await _published(state, scope, None)
    page = await service.list_suggestions(
        scope, active.run_id, status, video_id, PageRequest(cursor=cursor, limit=limit)
    )
    return _wrap(state, page, active)


@router.post(
    "/refresh-suggestions/{suggestion_id}/applied",
    operation_id="refresh_applied",
    response_model=RefreshSuggestionView,
)
async def refresh_applied(
    suggestion_id: str, state: State, scope: WorkspaceScope, body: RefreshAppliedIn | None = None
) -> RefreshSuggestionView:
    return await state.service(REFRESH).applied(scope, suggestion_id, body or RefreshAppliedIn())


@router.post(
    "/refresh-suggestions/{suggestion_id}/dismiss",
    operation_id="refresh_dismiss",
    response_model=RefreshSuggestionView,
)
async def refresh_dismiss(suggestion_id: str, state: State, scope: WorkspaceScope) -> RefreshSuggestionView:
    return await state.service(REFRESH).dismiss(scope, suggestion_id)


@router.get(
    "/clients/{client_id}/creators",
    operation_id="creators_list",
    response_model=ResultEnvelope[CreatorsData],
    dependencies=[Depends(only_params("window_days", "sort", *PAGING_PARAMS))],
)
async def creators_list(
    state: State,
    scope: ClientScope,
    window_days: Window = None,
    sort: CreatorSortParam | None = None,
    cursor: Cursor = None,
    limit: Limit = 25,
) -> ResultEnvelope[CreatorsData]:
    service = state.service(CREATORS)
    active = await _published(state, scope, window_days)
    data = await service.list_creators(
        scope, active.run_id, active.window_days, sort, PageRequest(cursor=cursor, limit=limit)
    )
    return _wrap(state, data, active)


@router.get(
    "/clients/{client_id}/early-signals",
    operation_id="early_signals_list",
    response_model=ResultEnvelope[list[EarlySignalView]],
    dependencies=[Depends(only_params("window_days"))],
)
async def early_signals_list(
    state: State, scope: ClientScope, window_days: Window = None
) -> ResultEnvelope[list[EarlySignalView]]:
    service = state.service(NEWS)
    active = await _published(state, scope, window_days)
    return _wrap(state, await service.early_signals(scope, active.run_id, active.window_days), active)


@router.get(
    "/clients/{client_id}/hygiene",
    operation_id="hygiene_list",
    response_model=ResultEnvelope[HygieneData],
    dependencies=[Depends(only_params(*PAGING_PARAMS))],
)
async def hygiene_list(
    state: State, scope: ClientScope, cursor: Cursor = None, limit: Limit = 25
) -> ResultEnvelope[HygieneData]:
    service = state.service(REFRESH)
    active = await _published(state, scope, None)
    data = await service.hygiene(scope, active.run_id, PageRequest(cursor=cursor, limit=limit))
    return _wrap(state, data, active)
