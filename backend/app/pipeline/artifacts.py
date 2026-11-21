import gzip
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock
from app.core.db import SessionFactory
from app.core.tenancy import TenantScope
from app.models.runs import MAX_ARTIFACT_PAYLOAD_BYTES, RunArtifact, RunArtifactBlob
from app.pipeline.fingerprint import is_degraded
from app.ports.artifacts import ArtifactKey


class ArtifactTooLarge(ValueError):
    pass


class ArtifactMissing(LookupError):
    pass


class ArtifactSchemaMismatch(LookupError):
    pass


@dataclass(frozen=True)
class StoredArtifact[T: BaseModel]:
    run_id: UUID
    fingerprint: str
    value: T


@dataclass(frozen=True)
class ArtifactExpiry:
    api_data_days: int
    other_days: int

    def expires_at(self, key: ArtifactKey[Any], now: datetime) -> datetime:
        days = self.api_data_days if key.contains_api_data else self.other_days
        return now + timedelta(days=days)


# above the inline cap a payload is gzipped into run_artifact_blobs; past this it is refused outright
MAX_BLOB_BYTES = 64 * 1024 * 1024
# the check constraint measures jsonb, which runs about 1.2x compact json, so spill with headroom
INLINE_JSON_BYTES = MAX_ARTIFACT_PAYLOAD_BYTES // 2
BLOB_POINTER = "$blob"


@dataclass(frozen=True)
class Encoded:
    payload: dict[str, Any]
    blob: tuple[str, bytes, int] | None = None


def serialise(key: ArtifactKey[Any], value: BaseModel) -> Encoded:
    if not isinstance(value, key.model):
        raise TypeError(f"artifact '{key.name}' must be {key.model.__name__}, got {type(value).__name__}")
    payload = value.model_dump(mode="json")
    raw = json.dumps(payload, separators=(",", ":")).encode()
    if len(raw) < INLINE_JSON_BYTES:
        return Encoded(payload)
    if len(raw) >= MAX_BLOB_BYTES:
        raise ArtifactTooLarge(
            f"artifact '{key.name}' is {len(raw)} bytes; artifacts hold pointers, not bulk data"
        )
    digest = hashlib.sha256(raw).hexdigest()
    return Encoded({BLOB_POINTER: digest, "bytes": len(raw)}, (digest, gzip.compress(raw), len(raw)))


async def load_payload(session: AsyncSession, row: RunArtifact) -> dict[str, Any]:
    """the artifact's json, following the pointer when the payload was stored as a blob."""
    digest = row.payload.get(BLOB_POINTER) if isinstance(row.payload, dict) else None
    if digest is None:
        return row.payload
    blob = await session.get(RunArtifactBlob, (row.client_id, digest))
    if blob is None:
        raise ArtifactMissing(f"artifact '{row.key}' points at a blob that no longer exists")
    return json.loads(gzip.decompress(blob.payload_gz))


async def _decode[T: BaseModel](session: AsyncSession, key: ArtifactKey[T], row: RunArtifact) -> T:
    if row.schema_version != key.schema_version:
        raise ArtifactSchemaMismatch(
            f"artifact '{key.name}' v{row.schema_version} stored, v{key.schema_version} expected"
        )
    try:
        return key.model.model_validate(await load_payload(session, row))
    except ValidationError as error:
        raise ArtifactSchemaMismatch(f"artifact '{key.name}' no longer validates") from error


class PgArtifactStore:
    """run_artifacts for one client; every query is fenced by workspace and client."""

    def __init__(
        self, session_factory: SessionFactory, scope: TenantScope, clock: Clock, expiry: ArtifactExpiry
    ) -> None:
        if scope.client_id is None:
            raise ValueError("artifact store needs a client-bound scope")
        self._session_factory = session_factory
        self._scope = scope
        self._clock = clock
        self._expiry = expiry

    @property
    def client_id(self) -> UUID:
        assert self._scope.client_id is not None
        return self._scope.client_id

    def upsert_statements(
        self, run_id: UUID, key: ArtifactKey[Any], value: BaseModel, fingerprint: str
    ) -> list:
        now = self._clock.now()
        encoded = serialise(key, value)
        expires_at = self._expiry.expires_at(key, now)
        statements = []
        if encoded.blob is not None:
            digest, payload_gz, size = encoded.blob
            blob = insert(RunArtifactBlob).values(
                workspace_id=self._scope.workspace_id,
                client_id=self.client_id,
                sha256=digest,
                payload_gz=payload_gz,
                size_bytes=size,
                expires_at=expires_at,
            )
            statements.append(
                blob.on_conflict_do_update(
                    index_elements=[RunArtifactBlob.client_id, RunArtifactBlob.sha256],
                    set_={"expires_at": func.greatest(RunArtifactBlob.expires_at, blob.excluded.expires_at)},
                )
            )
        values = {
            "workspace_id": self._scope.workspace_id,
            "client_id": self.client_id,
            "run_id": run_id,
            "key": key.name,
            "schema_version": key.schema_version,
            "fingerprint": fingerprint,
            "payload": encoded.payload,
            "contains_api_data": key.contains_api_data,
            "api_data_observed_at": now if key.contains_api_data else None,
            "expires_at": expires_at,
        }
        statement = insert(RunArtifact).values(**values)
        statements.append(
            statement.on_conflict_do_update(
                index_elements=[RunArtifact.run_id, RunArtifact.key],
                set_={
                    name: statement.excluded[name]
                    for name in ("schema_version", "fingerprint", "payload", "contains_api_data")
                }
                | {
                    "api_data_observed_at": statement.excluded.api_data_observed_at,
                    "expires_at": values["expires_at"],
                },
            )
        )
        return statements

    async def put[T: BaseModel](self, run_id: UUID, key: ArtifactKey[T], value: T, fingerprint: str) -> None:
        async with self._session_factory() as session:
            for statement in self.upsert_statements(run_id, key, value, fingerprint):
                await session.execute(statement)
            await session.commit()

    async def get[T: BaseModel](self, run_id: UUID, key: ArtifactKey[T]) -> T:
        async with self._session_factory() as session:
            row = await self._row(session, run_id, key.name)
            if row is None:
                raise ArtifactMissing(f"run has no '{key.name}' artifact")
            return await _decode(session, key, row)

    async def fingerprint_of(self, run_id: UUID, key: ArtifactKey[Any]) -> str | None:
        async with self._session_factory() as session:
            row = await self._row(session, run_id, key.name)
        if row is None or row.schema_version != key.schema_version:
            return None
        return row.fingerprint

    async def find_reusable[T: BaseModel](
        self, client_id: UUID, key: ArtifactKey[T], fingerprint: str, max_age: timedelta
    ) -> tuple[UUID, T] | None:
        if client_id != self.client_id or is_degraded(fingerprint):
            return None
        now = self._clock.now()
        statement = (
            self._scoped(select(RunArtifact))
            .where(
                RunArtifact.key == key.name,
                RunArtifact.fingerprint == fingerprint,
                RunArtifact.schema_version == key.schema_version,
                RunArtifact.created_at >= now - max_age,
                RunArtifact.expires_at > now,
            )
            .order_by(RunArtifact.created_at.desc())
            .limit(1)
        )
        async with self._session_factory() as session:
            row = (await session.execute(statement)).scalar_one_or_none()
            if row is None:
                return None
            try:
                return row.run_id, await _decode(session, key, row)
            except (ArtifactSchemaMismatch, ArtifactMissing):
                return None

    async def find_latest[T: BaseModel](
        self, key: ArtifactKey[T], *, exclude_run_id: UUID | None = None
    ) -> StoredArtifact[T] | None:
        statement = (
            self._scoped(select(RunArtifact))
            .where(
                RunArtifact.key == key.name,
                RunArtifact.schema_version == key.schema_version,
                RunArtifact.expires_at > self._clock.now(),
            )
            .order_by(RunArtifact.created_at.desc())
            .limit(1)
        )
        if exclude_run_id is not None:
            statement = statement.where(RunArtifact.run_id != exclude_run_id)
        async with self._session_factory() as session:
            row = (await session.execute(statement)).scalar_one_or_none()
            if row is None:
                return None
            try:
                value = await _decode(session, key, row)
            except (ArtifactSchemaMismatch, ArtifactMissing):
                return None
        return StoredArtifact(run_id=row.run_id, fingerprint=row.fingerprint, value=value)

    async def copy_into(
        self, session: AsyncSession, source_run_id: UUID, target_run_id: UUID, name: str
    ) -> None:
        # copies keep the source expiry: reuse never extends the life of api data
        source = await self._row(session, source_run_id, name)
        if source is None:
            raise ArtifactMissing(f"source run has no '{name}' artifact")
        statement = insert(RunArtifact).values(
            workspace_id=source.workspace_id,
            client_id=source.client_id,
            run_id=target_run_id,
            key=source.key,
            schema_version=source.schema_version,
            fingerprint=source.fingerprint,
            payload=source.payload,
            contains_api_data=source.contains_api_data,
            uses_authorized_data=source.uses_authorized_data,
            referenced_video_ids=source.referenced_video_ids,
            referenced_channel_ids=source.referenced_channel_ids,
            api_data_observed_at=source.api_data_observed_at,
            expires_at=source.expires_at,
        )
        upsert = statement.on_conflict_do_update(
            index_elements=[RunArtifact.run_id, RunArtifact.key],
            set_={
                "schema_version": statement.excluded.schema_version,
                "fingerprint": statement.excluded.fingerprint,
                "payload": statement.excluded.payload,
                "api_data_observed_at": statement.excluded.api_data_observed_at,
                "expires_at": statement.excluded.expires_at,
            },
        )
        await session.execute(upsert)

    def _scoped(self, statement):
        return statement.where(
            RunArtifact.workspace_id == self._scope.workspace_id, RunArtifact.client_id == self.client_id
        )

    async def _row(self, session: AsyncSession, run_id: UUID, name: str) -> RunArtifact | None:
        statement = self._scoped(select(RunArtifact)).where(
            RunArtifact.run_id == run_id, RunArtifact.key == name
        )
        return (await session.execute(statement)).scalar_one_or_none()


class RunArtifactReader:
    """what a stage sees: artifacts of its own run (reused and inherited ones are copied in)."""

    def __init__(self, store: PgArtifactStore, run_id: UUID) -> None:
        self._store = store
        self._run_id = run_id

    async def get[T: BaseModel](self, key: ArtifactKey[T]) -> T:
        return await self._store.get(self._run_id, key)

    async def get_optional[T: BaseModel](self, key: ArtifactKey[T]) -> T | None:
        try:
            return await self._store.get(self._run_id, key)
        except ArtifactMissing:
            return None
