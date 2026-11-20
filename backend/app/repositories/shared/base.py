from collections.abc import Iterator, Mapping, Sequence
from typing import Any

from sqlalchemy import Table
from sqlalchemy.ext.asyncio import AsyncSession

ASYNCPG_MAX_PARAMETERS = 32_767
MAX_ROWS_PER_STATEMENT = 1_000


def chunked(rows: Sequence[Mapping[str, Any]], width: int) -> Iterator[Sequence[Mapping[str, Any]]]:
    # multi-row values need identical keys, so rows are grouped by column set before sizing batches
    size = max(1, min(MAX_ROWS_PER_STATEMENT, ASYNCPG_MAX_PARAMETERS // max(width, 1)))
    groups: dict[frozenset[str], list[Mapping[str, Any]]] = {}
    for row in rows:
        groups.setdefault(frozenset(row), []).append(row)
    for group in groups.values():
        for start in range(0, len(group), size):
            yield group[start : start + size]


def validate_rows(
    table: Table, rows: Sequence[Mapping[str, Any]], required: Sequence[str]
) -> list[dict[str, Any]]:
    known = set(table.columns.keys())
    validated = []
    for row in rows:
        unknown = set(row) - known
        if unknown:
            raise ValueError(f"{table.name}: unknown columns {sorted(unknown)}")
        missing = [column for column in required if row.get(column) is None]
        if missing:
            raise ValueError(f"{table.name}: missing required values {missing}")
        validated.append(dict(row))
    return validated


class SharedRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
