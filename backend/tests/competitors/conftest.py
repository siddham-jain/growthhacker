import pytest

from app.core.config import CompetitorSettings
from app.services.competitors import registration  # noqa: F401
from app.services.competitors.context import DiscoveryContext, DiscoveryMeter
from tests.competitors.apollo import BUYER_QUESTIONS, CLIENT_ID, WORKSPACE_ID, apollo_profile
from tests.competitors.fakes import NOW

RUN_ID = __import__("uuid").UUID("00000000-0000-4000-8000-0000000000d1")


def make_context(settings: CompetitorSettings | None = None, **overrides) -> DiscoveryContext:
    settings = settings or CompetitorSettings()
    clock = iter(range(10_000))
    fields = dict(
        workspace_id=WORKSPACE_ID,
        client_id=CLIENT_ID,
        discovery_run_id=RUN_ID,
        pipeline_run_id=None,
        trigger="onboarding",
        profile=apollo_profile(),
        settings=settings,
        meter=DiscoveryMeter(
            settings.budget, settings.max_resolution_searches, monotonic=lambda: float(next(clock)) / 1000
        ),
        now=NOW,
        buyer_questions=list(BUYER_QUESTIONS),
    )
    fields.update(overrides)
    return DiscoveryContext(**fields)


@pytest.fixture
def settings() -> CompetitorSettings:
    return CompetitorSettings()
