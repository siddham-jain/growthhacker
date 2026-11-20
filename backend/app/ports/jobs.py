from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID


class JobStatus(StrEnum):
    deferred = "deferred"
    queued = "queued"
    in_progress = "in_progress"
    complete = "complete"
    not_found = "not_found"


class JobQueue(Protocol):
    async def enqueue_run(
        self, run_id: UUID, attempt: int, queue: str, defer_until: datetime | None = None
    ) -> str | None: ...

    async def abort(self, job_id: str, timeout_s: float) -> bool: ...

    async def status(self, job_id: str) -> JobStatus: ...

    async def depth(self, queue: str) -> int: ...
