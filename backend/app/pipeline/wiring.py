from dataclasses import dataclass
from typing import Any

import app.pipeline.definitions  # noqa: F401 - registers the pipeline definitions
from app.core.logging import get_logger
from app.pipeline.progress import RedisEventPublisher
from app.pipeline.registry import PipelineCatalog, validate_pipelines
from app.pipeline.runs import RunService
from app.pipeline.setup import SetupService
from app.ports.jobs import JobQueue

logger = get_logger(__name__)


@dataclass(frozen=True)
class PipelineServices:
    catalog: PipelineCatalog
    runs: RunService
    setup: SetupService
    publisher: RedisEventPublisher
    job_queue: JobQueue | None


def build_pipeline_services(
    container: Any, *, job_queue: JobQueue | None = None, catalog: PipelineCatalog | None = None
) -> PipelineServices:
    """validates the registries (a bad deploy refuses to start) and wires the run services."""
    settings = container.settings
    catalog = catalog or validate_pipelines(require_complete=settings.pipeline.require_complete)
    for name, reasons in catalog.unavailable.items():
        logger.warning("pipeline.unavailable", pipeline=name, reasons=list(reasons))
    publisher = RedisEventPublisher(container.redis)
    runs = RunService(
        session_factory=container.session_factory,
        catalog=catalog,
        settings=settings,
        config_source=container.adapter("run_config_source"),
        clock=container.clock,
        job_queue=job_queue,
        publisher=publisher,
        redis=container.redis,
    )
    setup = SetupService.from_container(container, runs)
    return PipelineServices(catalog=catalog, runs=runs, setup=setup, publisher=publisher, job_queue=job_queue)
