import pytest

from app.core.config import RecommendSettings
from app.services.recommend.builder import ContextBuilder
from app.services.recommend.context import RecommendContext
from app.services.recommend.inputs import RecommendInputs
from app.services.recommend.service import RecommendationService
from tests.recommend.fixtures import apollo_inputs


@pytest.fixture
def settings() -> RecommendSettings:
    return RecommendSettings()


@pytest.fixture
def inputs() -> RecommendInputs:
    return apollo_inputs()


@pytest.fixture
def service(settings: RecommendSettings) -> RecommendationService:
    return RecommendationService(settings)


def context_for(
    inputs: RecommendInputs, window: int = 30, settings: RecommendSettings | None = None
) -> RecommendContext:
    ctx = ContextBuilder(settings or RecommendSettings()).build(inputs, window)
    assert ctx is not None
    return ctx


@pytest.fixture
def ctx(inputs: RecommendInputs) -> RecommendContext:
    return context_for(inputs)
