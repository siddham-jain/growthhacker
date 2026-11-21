from datetime import datetime
from typing import Literal, Protocol

from app.domain.common import DomainModel
from app.domain.youtube import QuotaBucket
from app.ports.youtube import RequestContext

# who said no: our own caps (run, client, priority class, search pool) or the project's quota itself
QuotaReason = Literal["run_cap", "client_cap", "priority", "search_pool", "project", "google", "redis_down"]
# only these mean the data api cannot answer at all, rather than a deliberate cap
SOURCE_DOWN_REASONS: frozenset[QuotaReason] = frozenset({"project", "google", "redis_down"})


class QuotaExhausted(Exception):
    def __init__(self, bucket: QuotaBucket, resets_at: datetime, reason: QuotaReason = "project") -> None:
        super().__init__(f"{bucket.value} quota exhausted ({reason}) until {resets_at.isoformat()}")
        self.bucket = bucket
        self.resets_at = resets_at
        self.reason = reason


class QuotaReservation(DomainModel):
    bucket: QuotaBucket
    units: int
    used_after: int
    endpoint: str


class QuotaManager(Protocol):
    # raises QuotaExhausted when the day, priority-class or client cap would be exceeded
    async def reserve(
        self, bucket: QuotaBucket, units: int, ctx: RequestContext, endpoint: str
    ) -> QuotaReservation: ...

    # only for requests that provably never reached google
    async def refund(self, reservation: QuotaReservation, ctx: RequestContext) -> None: ...

    async def remaining(self, bucket: QuotaBucket) -> int: ...
