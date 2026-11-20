from datetime import datetime
from typing import Protocol

from app.domain.common import DomainModel
from app.domain.youtube import QuotaBucket
from app.ports.youtube import RequestContext


class QuotaExhausted(Exception):
    def __init__(self, bucket: QuotaBucket, resets_at: datetime) -> None:
        super().__init__(f"{bucket.value} quota exhausted until {resets_at.isoformat()}")
        self.bucket = bucket
        self.resets_at = resets_at


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
