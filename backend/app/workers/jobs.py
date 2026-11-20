import os
import socket
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import structlog
from arq import Retry

from app.bootstrap import load_registrations
from app.core.config import get_settings
from app.core.container import Container, build_container
from app.core.logging import configure_logging, get_logger
from app.pipeline.executor import RunExecutor
from app.pipeline.registry import PipelineCatalog
from app.pipeline.wiring import PipelineServices, build_pipeline_services
from app.workers.purge import purge_client_rows
from app.workers.queue import ArqJobQueue
from app.workers.reconciler import Reconciler
from app.workers.scheduler import DueScheduler

logger = get_logger(__name__)


@dataclass(frozen=True)
class WorkerState:
    container: Container
    services: PipelineServices
    queue: ArqJobQueue
    executor: RunExecutor
    reconciler: Reconciler
    scheduler: DueScheduler
    worker_id: str


def build_worker_state(container: Container, catalog: PipelineCatalog | None = None) -> WorkerState:
    queue: ArqJobQueue = container.adapter("job_queue")
    services = build_pipeline_services(container, job_queue=queue, catalog=catalog)
    settings = container.settings
    return WorkerState(
        container=container,
        services=services,
        queue=queue,
        executor=RunExecutor(container=container, runs=services.runs, publisher=services.publisher),
        reconciler=Reconciler(
            session_factory=container.session_factory,
            runs=services.runs,
            queue=queue,
            maintenance_queue=settings.workers.maintenance_queue,
        ),
        scheduler=DueScheduler(
            session_factory=container.session_factory,
            runs=services.runs,
            queue=queue,
            settings=settings,
            quota=container.quota_counter,
            clock=container.clock,
        ),
        worker_id=f"{socket.gethostname()}:{os.getpid()}",
    )


async def on_startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    configure_logging(settings.app)
    report = load_registrations()
    if report.missing:
        logger.info("worker.registrations_missing", modules=report.missing)
    container = build_container(settings)
    state = build_worker_state(container)
    await _warm_models(container)
    ctx["state"] = state
    logger.info("worker.started", worker_id=state.worker_id)


async def on_shutdown(ctx: dict[str, Any]) -> None:
    state: WorkerState | None = ctx.get("state")
    if state is not None:
        await state.container.aclose()


async def on_job_start(ctx: dict[str, Any]) -> None:
    structlog.contextvars.bind_contextvars(job_id=ctx.get("job_id"), job_try=ctx.get("job_try"))


async def after_job_end(ctx: dict[str, Any]) -> None:
    structlog.contextvars.unbind_contextvars("job_id", "job_try")


async def run_pipeline(ctx: dict[str, Any], run_id: str, attempt: int) -> str | None:
    state: WorkerState = ctx["state"]
    owner = f"{state.worker_id}:{ctx.get('job_id')}"
    structlog.contextvars.bind_contextvars(run_id=run_id)
    try:
        status = await state.executor.execute(UUID(run_id), int(attempt), owner)
    finally:
        structlog.contextvars.unbind_contextvars("run_id")
    return None if status is None else status.value


async def purge_client(ctx: dict[str, Any], client_id: str) -> str:
    state: WorkerState = ctx["state"]
    outcome = await purge_client_rows(state.container.session_factory, UUID(client_id))
    if outcome == "run_active":
        raise Retry(defer=30)
    logger.info("client.purge", client_id=client_id, outcome=outcome)
    return outcome


async def _warm_models(container: Container) -> None:
    # readiness waits for the embedding model so the first topics stage is not slowed by a cold load
    try:
        embedder = container.adapter("embedder")
    except LookupError:
        return
    warm = getattr(embedder, "warm", None)
    if warm is not None:
        await warm()
