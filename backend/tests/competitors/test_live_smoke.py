"""opt-in live discovery for apollo: at most 15 tavily credits, 2 search.list calls and 30 youtube units."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from app.adapters.llm import factory as llm_factory  # noqa: F401
from app.adapters.search import tavily  # noqa: F401
from app.adapters.youtube import factory as youtube_factory  # noqa: F401
from app.core.config import CompetitorBudgetSettings, CompetitorSettings, Settings
from app.core.container import Container, build_container
from app.domain.competitors import CandidateStatus
from app.services.competitors.service import CompetitorDiscoveryService, DiscoveryDeps
from tests.competitors.apollo import EXPECTED_COMPETITORS
from tests.competitors.conftest import make_context
from tests.conftest import make_settings

pytestmark = [pytest.mark.live, pytest.mark.db]

LIVE_SETTINGS = CompetitorSettings(
    budget=CompetitorBudgetSettings(
        youtube_units=30, search_calls=2, tavily_credits=15, llm_usd=0.30, wall_seconds=150
    ),
    max_resolution_searches=0,
    niche_queries_for_discovery=2,
    max_entities_to_resolve=12,
    uploads_sample_size=10,
)


@pytest.fixture
async def container(migrated_database: str) -> AsyncIterator[Container]:
    real = Settings()
    keys = {
        "youtube": real.youtube.api_key,
        "tavily": real.tavily.api_key,
        "anthropic": real.llm.anthropic.api_key,
    }
    if any(value is None for value in keys.values()):
        pytest.skip("live keys are not configured")
    container = build_container(
        make_settings(
            youtube={"api_key": keys["youtube"].get_secret_value()},
            tavily={"api_key": keys["tavily"].get_secret_value()},
            llm={"anthropic": {"api_key": keys["anthropic"].get_secret_value()}},
        )
    )
    yield container
    await container.aclose()


async def test_live_apollo_discovery(container: Container) -> None:
    deps = DiscoveryDeps(
        youtube=container.adapter("youtube"),
        web_search=container.adapter("web_search"),
        llm=container.adapter("llm"),
    )
    ctx = make_context(LIVE_SETTINGS)
    started = datetime.now(UTC)
    try:
        result = await CompetitorDiscoveryService(deps, LIVE_SETTINGS).discover(ctx)
    finally:
        await _forget_cache_rows(container, started)

    usage = result.usage
    assert usage.tavily_credits <= 15 and usage.search_calls <= 2 and usage.yt_units <= 30
    primaries = {
        candidate.primary_channel_id for candidate in result.candidates if candidate.primary_channel_id
    }
    report = [(c.brand_name, c.status.value, c.primary_channel_id, c.flags) for c in result.candidates]
    assert len(primaries & EXPECTED_COMPETITORS) >= 2, (report, usage, result.warnings)
    group = [
        candidate for candidate in result.candidates if candidate.brand_name.lower().startswith("apollo")
    ]
    assert all(candidate.status is CandidateStatus.excluded for candidate in group)


async def _forget_cache_rows(container: Container, started: datetime) -> None:
    # the smoke writes real rows into the shared cache; remove them so fixtures and sweeps see a clean cache
    async with container.uow() as uow:
        for statement in (
            "DELETE FROM yt_channels WHERE metadata_fetched_at >= :at",
            "DELETE FROM yt_search_cache WHERE fetched_at >= :at",
            "DELETE FROM yt_channel_resolutions WHERE resolved_at >= :at",
        ):
            await uow.session.execute(text(statement), {"at": started})
        await uow.commit()
