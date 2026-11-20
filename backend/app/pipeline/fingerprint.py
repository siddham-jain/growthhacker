import hashlib
import json
import math
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel

DEGRADED_SUFFIX = ":degraded"


def _encode(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, UUID | Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, set | frozenset):
        return sorted(_encode(item) for item in value)
    raise TypeError(f"cannot fingerprint value of type {type(value).__name__}")


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=_encode)


def sha256_hex(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def as_of_bucket(as_of: datetime, hours: float | None, tz: ZoneInfo) -> str | None:
    # buckets are aligned to the quota day so a new pacific day always recomputes i/o stages
    if not hours:
        return None
    local = as_of.astimezone(tz)
    if hours < 24:
        return f"{local.date().isoformat()}/{math.floor(local.hour / hours)}"
    days = hours / 24
    return f"d{math.floor(local.date().toordinal() / days)}"


def stage_fingerprint(
    *,
    name: str,
    version: int,
    schema_version: int,
    upstream: Mapping[str, str],
    inputs: Mapping[str, Any],
    bucket: str | None,
) -> str:
    return sha256_hex(
        {
            "stage": name,
            "version": version,
            "schema_version": schema_version,
            "upstream": dict(sorted(upstream.items())),
            "inputs": dict(inputs),
            "bucket": bucket,
        }
    )


def degraded(fingerprint: str) -> str:
    # degraded artifacts never match a reuse lookup, so the next run recomputes them
    return fingerprint + DEGRADED_SUFFIX


def is_degraded(fingerprint: str) -> bool:
    return fingerprint.endswith(DEGRADED_SUFFIX)
