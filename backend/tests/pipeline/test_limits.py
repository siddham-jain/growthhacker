import asyncio
import uuid

import pytest
from redis.asyncio import Redis

from app.ports.jobs import JobStatus
from app.workers.limits import RedisSemaphore, SemaphoreTimeout
from app.workers.queue import ArqJobQueue
from tests.conftest import TEST_REDIS_URL


async def test_semaphore_caps_concurrency_across_holders(redis: Redis, redis_prefix: str) -> None:
    semaphore = RedisSemaphore(redis, "youtube", 2, prefix=redis_prefix, lease_s=5, poll_s=0.01)
    active = 0
    peak = 0

    async def call() -> None:
        nonlocal active, peak
        async with semaphore.slot(wait_s=5):
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.05)
            active -= 1

    await asyncio.gather(*(call() for _ in range(6)))
    assert peak == 2
    assert await semaphore.holders() == 0


async def test_semaphore_reclaims_expired_leases_and_times_out(redis: Redis, redis_prefix: str) -> None:
    short = RedisSemaphore(redis, "llm", 1, prefix=redis_prefix, lease_s=0.2, poll_s=0.01)
    assert await short.try_acquire() is not None
    with pytest.raises(SemaphoreTimeout):
        async with short.slot(wait_s=0.05):
            pass
    await asyncio.sleep(0.25)
    assert await short.try_acquire() is not None


async def test_job_queue_dedupes_and_reports_status() -> None:
    queue_name = f"test:{uuid.uuid4().hex[:8]}:batch"
    queue = ArqJobQueue.from_url(TEST_REDIS_URL, [queue_name])
    run_id = uuid.uuid4()
    try:
        assert await queue.enqueue_run(run_id, 1, queue_name) == f"run:{run_id}:1"
        assert await queue.enqueue_run(run_id, 1, queue_name) is None
        assert await queue.status(f"run:{run_id}:1") is JobStatus.queued
        assert await queue.status(f"run:{run_id}:2") is JobStatus.not_found
        assert await queue.depth(queue_name) == 1
        stats = await queue.stats(queue_name)
        assert stats.depth == 1 and stats.oldest_age_s is not None and stats.worker_healthy is False
    finally:
        await queue.redis.delete(queue_name, f"arq:job:run:{run_id}:1")
        await queue.aclose()
