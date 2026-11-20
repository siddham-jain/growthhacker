import uuid
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.models.ledgers import ProviderUsageLedger, YtQuotaLedger
from app.repositories.shared.base import SharedRepository, chunked, validate_rows


class QuotaLedgerRepository(SharedRepository):
    """append-only; a retried job reuses its request_id so the row is written once."""

    required = ("request_id", "occurred_at", "pt_day", "bucket", "endpoint", "units", "scope")

    async def record(self, entries: Sequence[Mapping[str, Any]]) -> int:
        table = YtQuotaLedger.__table__
        validated = validate_rows(table, entries, self.required)
        written = 0
        for chunk in chunked(validated, len(table.columns)):
            statement = (
                insert(table).values(list(chunk)).on_conflict_do_nothing(index_elements=["request_id"])
            )
            written += (await self.session.execute(statement)).rowcount
        return written

    async def units_by_bucket(self, pt_day: date) -> dict[str, int]:
        statement = (
            select(YtQuotaLedger.bucket, func.sum(YtQuotaLedger.units))
            .where(YtQuotaLedger.pt_day == pt_day)
            .group_by(YtQuotaLedger.bucket)
        )
        return {bucket: int(units) for bucket, units in await self.session.execute(statement)}

    async def units_for_client(self, pt_day: date, client_id: uuid.UUID) -> dict[str, int]:
        statement = (
            select(YtQuotaLedger.bucket, func.sum(YtQuotaLedger.units))
            .where(YtQuotaLedger.pt_day == pt_day, YtQuotaLedger.client_id == client_id)
            .group_by(YtQuotaLedger.bucket)
        )
        return {bucket: int(units) for bucket, units in await self.session.execute(statement)}


class ProviderUsageLedgerRepository(SharedRepository):
    required = ("request_id", "occurred_at", "provider_kind", "provider", "operation", "outcome")

    async def record(self, entries: Sequence[Mapping[str, Any]]) -> int:
        table = ProviderUsageLedger.__table__
        validated = validate_rows(table, entries, self.required)
        written = 0
        for chunk in chunked(validated, len(table.columns)):
            statement = (
                insert(table).values(list(chunk)).on_conflict_do_nothing(index_elements=["request_id"])
            )
            written += (await self.session.execute(statement)).rowcount
        return written
