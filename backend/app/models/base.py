import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, ClassVar

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKeyConstraint,
    Integer,
    MetaData,
    Numeric,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, BYTEA, JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.db import NAMING_CONVENTION


class Zone(StrEnum):
    shared = "shared"
    tenant = "tenant"
    workspace = "workspace"
    global_ = "global"
    evaluation = "evaluation"


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {
        datetime: TIMESTAMP(timezone=True),
        date: Date(),
        uuid.UUID: UUID(as_uuid=True),
        dict[str, Any]: JSONB(),
        list[Any]: JSONB(),
        list[str]: ARRAY(Text()),
        list[uuid.UUID]: ARRAY(UUID(as_uuid=True)),
        bytes: BYTEA(),
        str: Text(),
        int: Integer(),
        Decimal: Numeric(12, 6),
    }

    __zone__: ClassVar[Zone]


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(primary_key=True, server_default=text("uuidv7()"))


def created_at_column() -> Mapped[datetime]:
    return mapped_column(nullable=False, server_default=func.now())


def updated_at_column() -> Mapped[datetime]:
    return mapped_column(nullable=False, server_default=func.now())


def jsonb_object() -> Mapped[dict[str, Any]]:
    return mapped_column(JSONB(), nullable=False, server_default=text("'{}'::jsonb"))


def jsonb_array() -> Mapped[list[Any]]:
    return mapped_column(JSONB(), nullable=False, server_default=text("'[]'::jsonb"))


def text_array() -> Mapped[list[str]]:
    return mapped_column(ARRAY(Text()), nullable=False, server_default=text("'{}'::text[]"))


def schema_version_column() -> Mapped[int]:
    return mapped_column(nullable=False, server_default=text("1"))


def check_in(column: str, values: tuple[str, ...] | list[str], *, nullable: bool = False) -> CheckConstraint:
    allowed = ", ".join(f"'{value}'" for value in values)
    condition = f"{column} IN ({allowed})"
    if nullable:
        condition = f"{column} IS NULL OR {condition}"
    return CheckConstraint(condition, name=column)


def client_fk() -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["workspace_id", "client_id"],
        ["clients.workspace_id", "clients.id"],
        ondelete="CASCADE",
    )


# client-scoped tables mix this in and add client_fk() to __table_args__
class TenantColumns:
    workspace_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    client_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
