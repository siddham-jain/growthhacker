from collections.abc import Sequence
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError
from sqlalchemy import func, select

from app.api import ids
from app.api.errors import (
    ClientDeletingError,
    CompetitorsOutOfRange,
    ConfigInvalidError,
    FieldError,
    NotFound,
    NotImplementedYet,
    RunCancellingError,
    RunInProgressError,
    RunNotCancellableError,
    RunNotRetryableError,
    TopicRefsRequired,
    WorkspaceBusyError,
    problem_response,
)
from app.api.schemas.clients import ClientOut
from app.api.schemas.runs import RunOut
from app.api.state import ApiState
from app.domain.runs import RunStatus
from app.models.runs import PipelineRun, StageRun
from app.models.tenancy import Client, OAuthToken
from app.pipeline.config_source import ConfigInvalid
from app.pipeline.registry import PipelineUnavailable
from app.pipeline.runs import (
    ACTIVE_VALUES,
    ClientDeleting,
    ClientNotFound,
    RunCancellingConflict,
    RunInProgress,
    RunNotCancellable,
    RunNotFound,
    RunNotRetryable,
    RunServiceError,
    WorkspaceBusy,
)
from app.pipeline.runs import job_id as run_job_id
from app.pipeline.setup import SetupInvalid, SetupNotReady, SetupNotWired

_RUN_ERRORS = {
    RunNotFound: NotFound,
    ClientNotFound: NotFound,
    ClientDeleting: ClientDeletingError,
    RunInProgress: RunInProgressError,
    RunCancellingConflict: RunCancellingError,
    RunNotCancellable: RunNotCancellableError,
    RunNotRetryable: RunNotRetryableError,
    WorkspaceBusy: WorkspaceBusyError,
}


def install_pipeline_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RunServiceError)
    async def run_error(request: Request, error: RunServiceError) -> JSONResponse:
        mapped = _RUN_ERRORS.get(type(error), RunInProgressError)
        extra = None if error.run_id is None else {"run_id": ids.encode(ids.RUN, error.run_id)}
        return problem_response(request, mapped(str(error), retry_after_s=error.retry_after_s, extra=extra))

    @app.exception_handler(PipelineUnavailable)
    async def unavailable(request: Request, error: PipelineUnavailable) -> JSONResponse:
        return problem_response(
            request,
            NotImplementedYet(
                f"'{error.pipeline}' runs are not wired into this deployment yet",
                extra={"pipeline": error.pipeline, "reasons": list(error.reasons)},
            ),
        )

    @app.exception_handler(SetupInvalid)
    async def setup_invalid(request: Request, error: SetupInvalid) -> JSONResponse:
        codes = [problem.split(":", 1)[0] for problem in error.problems]
        errors = [
            FieldError(loc=["body", code], msg=problem, type=code)
            for code, problem in zip(codes, error.problems, strict=True)
        ]
        mapped = CompetitorsOutOfRange if "competitors_out_of_range" in codes else TopicRefsRequired
        return problem_response(request, mapped(str(error), errors=errors))

    @app.exception_handler(SetupNotReady)
    async def setup_not_ready(request: Request, error: SetupNotReady) -> JSONResponse:
        return problem_response(request, ConfigInvalidError(str(error)))

    @app.exception_handler(ConfigInvalid)
    async def config_invalid(request: Request, error: ConfigInvalid) -> JSONResponse:
        return problem_response(request, ConfigInvalidError(str(error)))

    @app.exception_handler(SetupNotWired)
    async def setup_not_wired(request: Request, error: SetupNotWired) -> JSONResponse:
        return problem_response(request, NotImplementedYet(str(error)))


async def present_run(state: ApiState, run: PipelineRun, *, with_stages: bool = False) -> RunOut:
    stages = None
    order: Sequence[str] = ()
    if with_stages:
        async with state.container.session_factory() as session:
            stages = (
                (await session.execute(select(StageRun).where(StageRun.run_id == run.id))).scalars().all()
            )
        try:
            order = state.pipeline.runs.definition(run).stages
        except LookupError:
            order = ()
    return RunOut.from_row(
        run, stages=stages, stage_order=order, queue_position=await _queue_position(state, run)
    )


async def _queue_position(state: ApiState, run: PipelineRun) -> int | None:
    if run.status != RunStatus.queued.value or state.job_queue is None:
        return None
    try:
        rank = await state.job_queue.redis.zrank(run.queue, run_job_id(run.id, run.attempt))
    except (RedisError, OSError):
        return None
    return None if rank is None else int(rank) + 1


async def present_clients(state: ApiState, clients: Sequence[Client]) -> list[ClientOut]:
    if not clients:
        return []
    client_ids = [client.id for client in clients]
    async with state.container.session_factory() as session:
        active = dict(
            (
                await session.execute(
                    select(PipelineRun.client_id, PipelineRun.id).where(
                        PipelineRun.client_id.in_(client_ids), PipelineRun.status.in_(ACTIVE_VALUES)
                    )
                )
            ).all()
        )
        last_success = dict(
            (
                await session.execute(
                    select(PipelineRun.client_id, func.max(PipelineRun.finished_at))
                    .where(
                        PipelineRun.client_id.in_(client_ids),
                        PipelineRun.status.in_((RunStatus.succeeded.value, RunStatus.partial.value)),
                    )
                    .group_by(PipelineRun.client_id)
                )
            ).all()
        )
        connected = set(
            (
                await session.execute(
                    select(OAuthToken.client_id).where(
                        OAuthToken.client_id.in_(client_ids), OAuthToken.status == "active"
                    )
                )
            )
            .scalars()
            .all()
        )
    return [
        ClientOut.from_row(
            client,
            oauth_connected=client.id in connected,
            active_run_id=ids.encode_optional(ids.RUN, active.get(client.id)),
            last_success_at=last_success.get(client.id),
        )
        for client in clients
    ]


async def present_client(state: ApiState, client: Client) -> ClientOut:
    return (await present_clients(state, [client]))[0]


def run_location(run_id: UUID) -> str:
    return f"/api/v1/runs/{ids.encode(ids.RUN, run_id)}"
