import hashlib
import json
from typing import Any

from pydantic import BaseModel, Field

from app.api.errors import BadRequest, CursorMismatch
from app.api.signing import SignatureInvalid, Signer

CURSOR_PURPOSE = "cursor"


class Page[T](BaseModel):
    items: list[T]
    next_cursor: str | None = None
    limit: int
    total: int | None = None


class PageRequest(BaseModel):
    cursor: str | None = None
    limit: int = Field(default=25, ge=1, le=100)


def filter_hash(filters: dict[str, Any]) -> str:
    canonical = json.dumps(filters, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


class Cursors:
    """opaque base64url cursors over the last sort keys, bound to the filters they were issued for."""

    def __init__(self, signer: Signer) -> None:
        self._signer = signer

    def encode(self, keys: list[Any], filters: dict[str, Any]) -> str:
        return self._signer.sign(CURSOR_PURPOSE, {"k": keys, "f": filter_hash(filters)})

    def decode(self, cursor: str | None, filters: dict[str, Any]) -> list[Any] | None:
        if cursor is None:
            return None
        try:
            payload = self._signer.verify(CURSOR_PURPOSE, cursor)
        except SignatureInvalid:
            raise BadRequest("the cursor is invalid") from None
        if payload.get("f") != filter_hash(filters):
            raise CursorMismatch("the cursor was issued for different filters")
        keys = payload.get("k")
        if not isinstance(keys, list):
            raise BadRequest("the cursor is invalid")
        return keys
