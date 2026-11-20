import uuid
from collections.abc import Callable
from types import TracebackType
from typing import Any, Self

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import DatabaseSettings

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

type SessionFactory = async_sessionmaker[AsyncSession]


def build_engine(settings: DatabaseSettings, *, application_name: str | None = None) -> AsyncEngine:
    server_settings = {
        "timezone": "UTC",
        "application_name": application_name or settings.application_name,
        "statement_timeout": str(settings.statement_timeout_ms),
        "idle_in_transaction_session_timeout": str(settings.idle_in_tx_timeout_ms),
        "lock_timeout": str(settings.lock_timeout_ms),
    }
    connect_args: dict[str, Any] = {"server_settings": server_settings}
    options: dict[str, Any] = {"echo": settings.echo}
    if settings.use_external_pooler:
        # transaction-mode poolers cannot keep named prepared statements per connection
        connect_args["statement_cache_size"] = 0
        connect_args["prepared_statement_name_func"] = lambda: f"__asyncpg_{uuid.uuid4()}__"
        options["poolclass"] = NullPool
    else:
        options.update(
            pool_size=settings.pool_size,
            max_overflow=settings.max_overflow,
            pool_timeout=settings.pool_timeout_s,
            pool_pre_ping=True,
        )
    return create_async_engine(settings.url.get_secret_value(), connect_args=connect_args, **options)


def build_session_factory(engine: AsyncEngine) -> SessionFactory:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


class UnitOfWork:
    """one transaction; repositories share its session and never commit themselves."""

    def __init__(self, session_factory: SessionFactory, *, read_only: bool = False) -> None:
        self._session_factory = session_factory
        self._read_only = read_only
        self._session: AsyncSession | None = None

    @property
    def session(self) -> AsyncSession:
        if self._session is None:
            raise RuntimeError("UnitOfWork used outside 'async with'")
        return self._session

    async def __aenter__(self) -> Self:
        self._session = self._session_factory()
        if self._read_only:
            await self._session.execute(text("SET TRANSACTION READ ONLY"))
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            await self.session.rollback()
        finally:
            await self.session.close()
            self._session = None

    async def commit(self) -> None:
        if self._read_only:
            raise RuntimeError("read-only UnitOfWork cannot commit")
        await self.session.commit()

    async def rollback(self) -> None:
        await self.session.rollback()

    def repository[R](self, repository_type: Callable[..., R], *args: Any) -> R:
        return repository_type(self.session, *args)
