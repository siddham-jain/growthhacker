import types
from collections.abc import Sequence
from typing import Any, ClassVar

from sqlalchemy import Select, delete, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.core.registry import Registry
from app.core.tenancy import TenantScope
from app.models.base import Base

tenant_repository_registry: Registry[type["TenantRepository[Any]"]] = Registry("tenant_repository_registry")


class TenantMismatchError(PermissionError):
    pass


class TenantRepository[M: Base]:
    """every query passes through _scoped(); ids from another tenant behave as missing."""

    model: ClassVar[type[Base]]
    client_column_name: ClassVar[str] = "client_id"

    def __init_subclass__(cls, register: bool = True, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if register and "model" in cls.__dict__:
            tenant_repository_registry.add(cls.model.__tablename__, cls)

    def __init__(self, session: AsyncSession, scope: TenantScope) -> None:
        if not isinstance(scope, TenantScope):
            raise TypeError("tenant repositories require a TenantScope")
        self.session = session
        self.scope = scope

    @classmethod
    def for_model(cls, model: type[M]) -> type["TenantRepository[M]"]:
        if "workspace_id" not in model.__table__.columns:
            raise TypeError(f"{model.__tablename__} has no workspace_id; it is not tenant-scoped")
        return types.new_class(
            f"{model.__name__}ScopedRepository",
            (cls,),
            {"register": False},
            lambda ns: ns.update(model=model),
        )

    @classmethod
    def _client_column(cls) -> InstrumentedAttribute[Any] | None:
        return getattr(cls.model, cls.client_column_name, None)

    def _scoped[S: Select[Any]](self, statement: S) -> S:
        model = self.model
        statement = statement.where(model.workspace_id == self.scope.workspace_id)
        client_column = self._client_column()
        if client_column is not None and self.scope.client_id is not None:
            statement = statement.where(client_column == self.scope.client_id)
        return statement

    def _primary_key_filter(self, key: Any) -> list[Any]:
        columns = inspect(self.model).primary_key
        values = key if isinstance(key, tuple) else (key,)
        if len(values) != len(columns):
            raise ValueError(f"{self.model.__tablename__} primary key has {len(columns)} column(s)")
        return [column == value for column, value in zip(columns, values, strict=True)]

    async def get(self, key: Any) -> M | None:
        statement = self._scoped(select(self.model).where(*self._primary_key_filter(key)))
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def list(self, *, limit: int = 100, offset: int = 0) -> Sequence[M]:
        statement = self._scoped(select(self.model)).limit(limit).offset(offset)
        return (await self.session.execute(statement)).scalars().all()

    def add(self, entity: M) -> M:
        if getattr(entity, "workspace_id", None) is None:
            entity.workspace_id = self.scope.workspace_id
        if entity.workspace_id != self.scope.workspace_id:
            raise TenantMismatchError("entity belongs to another workspace")
        client_attribute = self.client_column_name
        if self._client_column() is not None and self.scope.client_id is not None:
            if getattr(entity, client_attribute, None) is None:
                setattr(entity, client_attribute, self.scope.client_id)
            if getattr(entity, client_attribute) != self.scope.client_id:
                raise TenantMismatchError("entity belongs to another client")
        self.session.add(entity)
        return entity

    async def delete(self, key: Any) -> bool:
        model = self.model
        statement = delete(model).where(
            *self._primary_key_filter(key), model.workspace_id == self.scope.workspace_id
        )
        client_column = self._client_column()
        if client_column is not None and self.scope.client_id is not None:
            statement = statement.where(client_column == self.scope.client_id)
        result = await self.session.execute(statement)
        return result.rowcount > 0
