import pytest

from app.domain.refresh import DescriptionRefresh
from tests.refresh import scenarios
from tests.refresh.fixtures import engine


@pytest.fixture
async def runable_output():
    return await engine().run(scenarios.runable())


@pytest.fixture
async def sample_refresh(runable_output) -> DescriptionRefresh:
    return next(refresh for refresh in runable_output.refreshes if refresh.video_id == scenarios.AI_VIDEO)
