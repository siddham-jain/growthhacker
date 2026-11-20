from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from typing import Protocol, TypeVar

T = TypeVar("T")


class Lease(Protocol):
    key: str
    token: str

    async def renew(self, ttl_s: float) -> bool: ...


class LockManager(Protocol):
    def acquire(
        self, key: str, ttl_s: float, wait_s: float | None = None
    ) -> AbstractAsyncContextManager[Lease]: ...

    async def single_flight(self, key: str, ttl_s: float, fn: Callable[[], Awaitable[T]]) -> T: ...
