from dataclasses import dataclass

from sqlalchemy import exists, select

from app.core.db import SessionFactory
from app.core.logging import get_logger
from app.domain.runs import ClientLifecycle
from app.models.runs import PipelineRun
from app.models.tenancy import Client
from app.pipeline.runs import ACTIVE_VALUES, RunService
from app.pipeline.runs import job_id as run_job_id
from app.ports.jobs import JobStatus
from app.workers.queue import PURGE_CLIENT, ArqJobQueue

logger = get_logger(__name__)


@dataclass
class ReconcileReport:
    recovered: int = 0
    requeued: int = 0
    aborted: int = 0
    purges: int = 0


class Reconciler:
    """repairs what crashes, lost redis jobs and stuck cancels leave behind (plan 11 §5.9e)."""

    def __init__(
        self, *, session_factory: SessionFactory, runs: RunService, queue: ArqJobQueue, maintenance_queue: str
    ) -> None:
        self._session_factory = session_factory
        self._runs = runs
        self._queue = queue
        self._maintenance_queue = maintenance_queue

    async def run(self) -> ReconcileReport:
        report = ReconcileReport()
        for run in await self._runs.stale_leases():
            if await self._runs.recover(run) is not None:
                report.recovered += 1
        for run in await self._runs.orphaned():
            if await self._queue.status(run_job_id(run.id, run.attempt)) is JobStatus.not_found:
                await self._runs.enqueue(run)
                report.requeued += 1
        for run in await self._runs.overdue_cancels():
            if run.arq_job_id and await self._queue.abort(run.arq_job_id, timeout_s=0):
                report.aborted += 1
        for client_id in await self._deleting_clients():
            await self._queue.enqueue(
                PURGE_CLIENT, str(client_id), job_id=f"purge:{client_id}", queue=self._maintenance_queue
            )
            report.purges += 1
        if report.recovered or report.requeued or report.aborted:
            logger.info("reconcile.report", **report.__dict__)
        return report

    async def _deleting_clients(self) -> list:
        active = exists().where(PipelineRun.client_id == Client.id, PipelineRun.status.in_(ACTIVE_VALUES))
        statement = (
            select(Client.id).where(Client.lifecycle == ClientLifecycle.deleting.value, ~active).limit(50)
        )
        async with self._session_factory() as session:
            return list((await session.execute(statement)).scalars().all())
