from collections.abc import AsyncIterator

import pytest

from app.core.container import Container
from app.core.tenancy import TenantScope
from tests.pipeline.support import create_tenant, drop_workspace, make_container


@pytest.fixture
async def container(migrated_database: str) -> AsyncIterator[Container]:
    built = make_container()
    yield built
    await built.aclose()


@pytest.fixture
async def tenant(container: Container) -> AsyncIterator[TenantScope]:
    scope = await create_tenant(container)
    yield scope
    await drop_workspace(container, scope.workspace_id)
