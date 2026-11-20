import html
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

from app.core.clock import Clock
from app.core.config import Settings
from app.core.redis import RateLimiter
from app.core.registry import freshness_source_registry
from app.ports.freshness import FreshnessSource, NewsFetchResult

SNIPPET_MAX_CHARS = 1_500
EXHAUSTED_STATUSES = frozenset({432, 433})
TAG_PATTERN = re.compile(r"<[^>]+>")
WHITESPACE_PATTERN = re.compile(r"\s+")


@dataclass(frozen=True)
class FreshnessDeps:
    http: httpx.AsyncClient
    settings: Settings
    clock: Clock
    rate_limiter: RateLimiter | None = None


def build_source(key: str, deps: FreshnessDeps) -> FreshnessSource:
    return freshness_source_registry.get(key)(deps)


def clean_text(value: str | None, max_chars: int = SNIPPET_MAX_CHARS) -> str:
    if not value:
        return ""
    text = WHITESPACE_PATTERN.sub(" ", html.unescape(TAG_PATTERN.sub(" ", value))).strip()
    return text[:max_chars]


def parse_rfc1123(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def failed_result(status_code: int | None, detail: str, request_id: str | None = None) -> NewsFetchResult:
    if status_code == 429:
        status = "rate_limited"
    elif status_code in EXHAUSTED_STATUSES:
        status = "exhausted"
    else:
        status = "error"
    return NewsFetchResult(
        items=[], cost=0.0, status=status, error=detail[:300], provider_request_id=request_id
    )


async def respect_interval(deps: FreshnessDeps, source_key: str, min_interval_seconds: float) -> None:
    # politeness holds across workers through the shared redis token bucket
    if deps.rate_limiter is None or min_interval_seconds <= 0:
        return
    await deps.rate_limiter.take(
        f"freshness:{source_key}", rate_per_s=1.0 / min_interval_seconds, capacity=1.0
    )


async def get_json(deps: FreshnessDeps, url: str, params: dict[str, str | int]) -> httpx.Response:
    return await deps.http.get(url, params=params, timeout=httpx.Timeout(20.0, connect=5.0))
