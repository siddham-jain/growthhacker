import re
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, CheckConstraint, Identity, Table, insert
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import TenantScope
from app.models.tenancy import Client, Workspace

SAMPLE_TIME = datetime(2026, 11, 20, 12, tzinfo=UTC)
IN_LIST = re.compile(r"^\(?(\w+) IN \(([^)]*)\)\)?$")
SCOPE_ONLY_TARGETS = {"clients", "workspaces", "users"}


def _allowed_values(table: Table) -> dict[str, str]:
    allowed = {}
    for constraint in table.constraints:
        if isinstance(constraint, CheckConstraint):
            match = IN_LIST.match(str(constraint.sqltext).strip())
            if match:
                allowed[match[1]] = match[2].split(",")[0].strip().strip("'")
    return allowed


def _value_for(column, allowed: dict[str, str]):
    if column.name in allowed:
        raw = allowed[column.name]
        return int(raw) if raw.isdigit() else raw
    if isinstance(column.type, JSON):
        return {}
    if isinstance(column.type, ARRAY):
        return []
    python_type = column.type.python_type
    samples = {
        uuid.UUID: uuid.uuid4(),
        datetime: SAMPLE_TIME,
        date: SAMPLE_TIME.date(),
        bool: False,
        int: 1,
        float: 1.0,
        Decimal: Decimal("1"),
        bytes: b"x",
    }
    if python_type is str:
        return f"v{uuid.uuid4().hex[:10]}"
    return samples[python_type]


async def insert_row(
    session: AsyncSession,
    table: Table,
    scope: TenantScope,
    values: dict[str, Any] | None = None,
    *,
    parents: dict | None = None,
) -> dict[str, Any]:
    # fills required columns (first allowed value for enum checks) and creates required parent rows
    parents = {} if parents is None else parents
    row = {name: getattr(scope, name) for name in ("workspace_id", "client_id") if name in table.columns}
    row.update(values or {})
    for fk in table.foreign_key_constraints:
        target = fk.referred_table
        if target.name in SCOPE_ONLY_TARGETS or target is table or all(c.nullable for c in fk.columns):
            continue
        if all(column.name in row for column in fk.columns):
            continue
        if (target.name, scope) not in parents:
            parents[(target.name, scope)] = await insert_row(session, target, scope, parents=parents)
        parent = parents[(target.name, scope)]
        for local, element in zip(fk.columns, fk.elements, strict=True):
            row[local.name] = parent[element.column.name]
    allowed = _allowed_values(table)
    for column in table.columns:
        if column.name in row or column.nullable or column.server_default is not None:
            continue
        if isinstance(column.identity, Identity):
            continue
        row[column.name] = _value_for(column, allowed)
    result = await session.execute(insert(table).values(**row).returning(*table.columns))
    return dict(result.mappings().one())


async def _tenant(session: AsyncSession, label: str) -> TenantScope:
    workspace_id = (
        await session.execute(
            insert(Workspace)
            .values(name=label, slug=f"{label}-{uuid.uuid4().hex[:6]}")
            .returning(Workspace.id)
        )
    ).scalar_one()
    client_id = (
        await session.execute(
            insert(Client)
            .values(workspace_id=workspace_id, name=label, slug=label, website_url=f"https://{label}.example")
            .returning(Client.id)
        )
    ).scalar_one()
    return TenantScope(workspace_id=workspace_id, client_id=client_id)


async def create_tenant(session: AsyncSession, label: str, **client_values: Any) -> TenantScope:
    workspace_id = (
        await session.execute(
            insert(Workspace)
            .values(name=label, slug=f"{label}-{uuid.uuid4().hex[:6]}")
            .returning(Workspace.id)
        )
    ).scalar_one()
    values = {"name": label, "slug": label, "website_url": f"https://{label}.example", **client_values}
    client_id = (
        await session.execute(insert(Client).values(workspace_id=workspace_id, **values).returning(Client.id))
    ).scalar_one()
    return TenantScope(workspace_id=workspace_id, client_id=client_id)
