import hashlib
import json
from datetime import timedelta
from typing import Any
from uuid import UUID

from fastapi.responses import JSONResponse, Response
from sqlalchemy import delete, select, text, update
from sqlalchemy.dialects.postgresql import insert

from app.api.errors import BadRequest, IdempotencyInProgress, IdempotencyKeyReused
from app.core.db import SessionFactory
from app.models.runs import IdempotencyKey

REPLAYED_HEADERS = ("location", "x-run-deduplicated", "etag")
IN_PROGRESS = "in_progress"
COMPLETED = "completed"


def parse_key(header: str) -> str:
    # draft-08 sends an sf-string, so accept the quoted form as well as a bare token
    value = header.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        value = value[1:-1]
    if not 1 <= len(value) <= 255 or any(ord(char) < 0x21 or ord(char) > 0x7E for char in value):
        raise BadRequest("Idempotency-Key must be 1-255 visible ascii characters")
    return value


def request_fingerprint(method: str, path: str, workspace_id: UUID, body: bytes) -> str:
    try:
        canonical: Any = json.loads(body) if body else None
        encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    except ValueError:
        encoded = body.decode("latin-1")
    return hashlib.sha256(f"{method}\n{path}\n{workspace_id}\n{encoded}".encode()).hexdigest()


class IdempotentRequest:
    """one claimed key; replay holds the stored response when the key already completed."""

    def __init__(
        self,
        session_factory: SessionFactory | None = None,
        *,
        workspace_id: UUID | None = None,
        key: str | None = None,
        replay: Response | None = None,
        max_body_bytes: int = 64 * 1024,
    ) -> None:
        self._session_factory = session_factory
        self._workspace_id = workspace_id
        self.key = key
        self.replay = replay
        self._max_body_bytes = max_body_bytes
        self._completed = replay is not None or key is None

    @property
    def active(self) -> bool:
        return self.key is not None and self.replay is None

    async def complete(self, response: JSONResponse) -> JSONResponse:
        if not self.active:
            return response
        assert self._session_factory is not None
        body = bytes(response.body)
        headers = {
            name: value for name, value in response.headers.items() if name.lower() in REPLAYED_HEADERS
        }
        async with self._session_factory() as session, session.begin():
            if len(body) > self._max_body_bytes:
                await session.execute(self._row_filter(delete(IdempotencyKey)))
            else:
                await session.execute(
                    self._row_filter(update(IdempotencyKey)).values(
                        status=COMPLETED,
                        response_status=response.status_code,
                        response_headers=headers,
                        response_body=json.loads(body) if body else None,
                        resource_id=headers.get("location"),
                    )
                )
        self._completed = True
        return response

    async def release(self) -> None:
        # the handler failed: free the key so the client may retry with it
        if self._completed or self._session_factory is None:
            return
        async with self._session_factory() as session, session.begin():
            await session.execute(
                self._row_filter(delete(IdempotencyKey)).where(IdempotencyKey.status == IN_PROGRESS)
            )

    def _row_filter(self, statement):
        return statement.where(
            IdempotencyKey.workspace_id == self._workspace_id, IdempotencyKey.key == self.key
        )


async def claim(
    session_factory: SessionFactory,
    *,
    workspace_id: UUID,
    key: str,
    method: str,
    path: str,
    body: bytes,
    ttl: timedelta,
    max_body_bytes: int,
) -> IdempotentRequest:
    fingerprint = request_fingerprint(method, path, workspace_id, body)
    async with session_factory() as session, session.begin():
        now = (await session.execute(text("SELECT now()"))).scalar_one()
        await session.execute(
            delete(IdempotencyKey).where(
                IdempotencyKey.workspace_id == workspace_id,
                IdempotencyKey.key == key,
                IdempotencyKey.expires_at <= now,
            )
        )
        inserted = (
            await session.execute(
                insert(IdempotencyKey)
                .values(
                    workspace_id=workspace_id,
                    key=key,
                    fingerprint=fingerprint,
                    method=method,
                    path=path,
                    status=IN_PROGRESS,
                    expires_at=now + ttl,
                )
                .on_conflict_do_nothing()
                .returning(IdempotencyKey.key)
            )
        ).scalar_one_or_none()
        if inserted is not None:
            return IdempotentRequest(
                session_factory, workspace_id=workspace_id, key=key, max_body_bytes=max_body_bytes
            )
        row = (
            await session.execute(
                select(IdempotencyKey).where(
                    IdempotencyKey.workspace_id == workspace_id, IdempotencyKey.key == key
                )
            )
        ).scalar_one()
    if row.fingerprint != fingerprint:
        raise IdempotencyKeyReused("this Idempotency-Key was used with a different request")
    if row.status != COMPLETED:
        raise IdempotencyInProgress("the original request with this key is still running", retry_after_s=2)
    headers = {**(row.response_headers or {}), "Idempotent-Replayed": "true"}
    replay = JSONResponse(row.response_body, status_code=row.response_status or 200, headers=headers)
    return IdempotentRequest(workspace_id=workspace_id, key=key, replay=replay)
