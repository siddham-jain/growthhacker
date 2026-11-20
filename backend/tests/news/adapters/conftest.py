from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx
import pytest

from app.adapters.freshness.base import FreshnessDeps
from app.core.clock import FrozenClock
from app.ports.freshness import PlannedQuery
from tests.conftest import make_settings

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
SINCE = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)


def planned(source_key: str, query: str, **params) -> PlannedQuery:
    return PlannedQuery(
        source_key=source_key,
        normalized_query=query,
        params=params,
        query_hash=f"hash-{source_key}-{query}",
        cadence_minutes=360,
    )


@pytest.fixture
async def http() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient() as client:
        yield client


@pytest.fixture
def deps(http: httpx.AsyncClient) -> FreshnessDeps:
    return FreshnessDeps(
        http=http, settings=make_settings(tavily={"api_key": "tvly-test"}), clock=FrozenClock(NOW)
    )
