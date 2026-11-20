from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel


@dataclass(frozen=True, slots=True)
class ArtifactKey[T: BaseModel]:
    name: str
    model: type[T]
    schema_version: int
    contains_api_data: bool


class ArtifactReader(Protocol):
    async def get[T: BaseModel](self, key: ArtifactKey[T]) -> T: ...

    async def get_optional[T: BaseModel](self, key: ArtifactKey[T]) -> T | None: ...


class ArtifactStore(Protocol):
    async def put[T: BaseModel](
        self, run_id: UUID, key: ArtifactKey[T], value: T, fingerprint: str
    ) -> None: ...

    async def get[T: BaseModel](self, run_id: UUID, key: ArtifactKey[T]) -> T: ...

    async def find_reusable[T: BaseModel](
        self, client_id: UUID, key: ArtifactKey[T], fingerprint: str, max_age: timedelta
    ) -> tuple[UUID, T] | None: ...
