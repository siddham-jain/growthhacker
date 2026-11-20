import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.models.youtube_cache import PARTITIONED_TABLES

PARTITION_SUFFIX = re.compile(r"_p(\d{8})$")


@dataclass(frozen=True)
class DailyPartition:
    parent: str
    day: date

    @property
    def name(self) -> str:
        return f"{self.parent}_p{self.day:%Y%m%d}"

    @property
    def lower_bound(self) -> datetime:
        return datetime.combine(self.day, time.min, tzinfo=UTC)

    @property
    def upper_bound(self) -> datetime:
        return self.lower_bound + timedelta(days=1)

    def create_sql(self) -> str:
        return (
            f'CREATE TABLE IF NOT EXISTS "{self.name}" PARTITION OF "{self.parent}" '
            f"FOR VALUES FROM ('{self.lower_bound.isoformat()}') TO ('{self.upper_bound.isoformat()}')"
        )


def _require_partitioned(parent: str) -> None:
    if parent not in PARTITIONED_TABLES:
        raise ValueError(f"'{parent}' is not a daily-partitioned table")


def partitions_between(parent: str, first_day: date, last_day: date) -> list[DailyPartition]:
    _require_partitioned(parent)
    days = (last_day - first_day).days
    return [DailyPartition(parent, first_day + timedelta(days=offset)) for offset in range(days + 1)]


async def existing_partitions(
    connection: AsyncConnection | AsyncSession, parent: str
) -> list[DailyPartition]:
    _require_partitioned(parent)
    rows = await connection.execute(
        text(
            "SELECT child.relname FROM pg_inherits "
            "JOIN pg_class parent ON parent.oid = pg_inherits.inhparent "
            "JOIN pg_class child ON child.oid = pg_inherits.inhrelid "
            "WHERE parent.relname = :parent"
        ),
        {"parent": parent},
    )
    partitions = []
    for (relname,) in rows:
        match = PARTITION_SUFFIX.search(relname)
        if match:
            partitions.append(DailyPartition(parent, datetime.strptime(match[1], "%Y%m%d").date()))
    return sorted(partitions, key=lambda partition: partition.day)


async def ensure_partitions(
    connection: AsyncConnection | AsyncSession,
    parent: str,
    *,
    today: date,
    days_ahead: int,
    days_behind: int = 1,
) -> list[str]:
    wanted = partitions_between(
        parent, today - timedelta(days=days_behind), today + timedelta(days=days_ahead)
    )
    present = {partition.name for partition in await existing_partitions(connection, parent)}
    created = []
    for partition in wanted:
        if partition.name not in present:
            await connection.execute(text(partition.create_sql()))
            created.append(partition.name)
    return created


async def drop_partitions_before(
    connection: AsyncConnection | AsyncSession, parent: str, *, cutoff: datetime
) -> list[str]:
    dropped = []
    for partition in await existing_partitions(connection, parent):
        if partition.upper_bound <= cutoff:
            await connection.execute(text(f'ALTER TABLE "{parent}" DETACH PARTITION "{partition.name}"'))
            await connection.execute(text(f'DROP TABLE "{partition.name}"'))
            dropped.append(partition.name)
    return dropped
