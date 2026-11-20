from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from arq.connections import ArqRedis
from arq.constants import abort_jobs_ss, in_progress_key_prefix, result_key_prefix
from arq.jobs import Job
from arq.utils import timestamp_ms

from app.core.registry import adapter_registry
from app.pipeline.runs import job_id as run_job_id
from app.ports.jobs import JobStatus

RUN_PIPELINE = "run_pipeline"
PURGE_CLIENT = "purge_client"


@dataclass(frozen=True)
class QueueStats:
    name: str
    depth: int
    oldest_age_s: float | None
    worker_healthy: bool


class ArqJobQueue:
    """JobQueue port over arq; payloads carry primitives only so they survive deploys."""

    def __init__(self, redis: ArqRedis, queues: Sequence[str]) -> None:
        self._redis = redis
        self._queues = tuple(queues)

    @classmethod
    def from_url(cls, url: str, queues: Sequence[str]) -> "ArqJobQueue":
        return cls(ArqRedis.from_url(url), queues)

    @property
    def redis(self) -> ArqRedis:
        return self._redis

    async def enqueue_run(
        self, run_id: UUID, attempt: int, queue: str, defer_until: datetime | None = None
    ) -> str | None:
        job = await self._redis.enqueue_job(
            RUN_PIPELINE,
            str(run_id),
            attempt,
            _job_id=run_job_id(run_id, attempt),
            _queue_name=queue,
            _defer_until=defer_until,
        )
        return None if job is None else job.job_id

    async def enqueue(self, function: str, *args: Any, job_id: str, queue: str) -> str | None:
        job = await self._redis.enqueue_job(function, *args, _job_id=job_id, _queue_name=queue)
        return None if job is None else job.job_id

    async def abort(self, job_id: str, timeout_s: float) -> bool:
        if timeout_s <= 0:
            await self._redis.zadd(abort_jobs_ss, {job_id: timestamp_ms()})
            return True
        for queue in self._queues:
            job = Job(job_id, self._redis, _queue_name=queue)
            if await job.status() in {JobStatus.queued, JobStatus.deferred, JobStatus.in_progress}:
                try:
                    return await job.abort(timeout=timeout_s)
                except TimeoutError:
                    return False
        return False

    async def status(self, job_id: str) -> JobStatus:
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.exists(result_key_prefix + job_id)
            pipe.exists(in_progress_key_prefix + job_id)
            for queue in self._queues:
                pipe.zscore(queue, job_id)
            complete, in_progress, *scores = await pipe.execute()
        if complete:
            return JobStatus.complete
        if in_progress:
            return JobStatus.in_progress
        score = next((score for score in scores if score), None)
        if score is None:
            return JobStatus.not_found
        return JobStatus.deferred if score > timestamp_ms() else JobStatus.queued

    async def depth(self, queue: str) -> int:
        return int(await self._redis.zcard(queue))

    async def stats(self, queue: str) -> QueueStats:
        oldest = await self._redis.zrange(queue, 0, 0, withscores=True)
        age = max((timestamp_ms() - float(oldest[0][1])) / 1000, 0.0) if oldest else None
        # arq workers refresh "<queue>:health-check" every health_check_interval
        healthy = bool(await self._redis.exists(f"{queue}:health-check"))
        return QueueStats(name=queue, depth=await self.depth(queue), oldest_age_s=age, worker_healthy=healthy)

    async def aclose(self) -> None:
        await self._redis.aclose()


@adapter_registry.register("job_queue", "arq")
def build_arq_job_queue(container: Any) -> ArqJobQueue:
    workers = container.settings.workers
    return ArqJobQueue.from_url(
        container.settings.redis.url.get_secret_value(),
        (workers.interactive_queue, workers.batch_queue, workers.maintenance_queue),
    )
