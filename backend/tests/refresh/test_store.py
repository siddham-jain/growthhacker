from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.refresh import RefreshImpact, RefreshStatus
from app.models.runs import PipelineRun
from app.services.refresh.lifecycle import StatusChange
from app.services.refresh.links.health import CachedLink
from app.services.refresh.links.status import LinkStatus, LinkStatusClass
from app.services.refresh.store import (
    DescriptionRefreshRepository,
    SqlLinkStatusCache,
    SqlRefreshStore,
)
from tests.foundation.factories import create_tenant, insert_row
from tests.refresh.fixtures import NOW

pytestmark = pytest.mark.db


@pytest.fixture
async def tenant(session: AsyncSession):
    scope = await create_tenant(session, "runable")
    run = await insert_row(session, PipelineRun.__table__, scope)
    return scope, run["id"]


async def test_refresh_round_trip_and_transitions(session_factory, tenant, runable_output) -> None:
    scope, run_id = tenant
    store = SqlRefreshStore(session_factory, scope)
    refreshes = [
        refresh.model_copy(update={"client_id": scope.client_id, "run_id": run_id})
        for refresh in runable_output.refreshes
    ]
    eligibility = runable_output.eligibility.model_copy(
        update={"client_id": scope.client_id, "run_id": run_id}
    )
    await store.save_run(refreshes, eligibility, [], [], NOW)
    history, impacts, last_eval = await store.load_history(NOW - timedelta(days=60))
    loaded = {item.refresh.id: item.refresh for item in history}
    assert set(loaded) == {refresh.id for refresh in refreshes}
    for refresh in refreshes:
        # priority is a real column, so only its float4 value survives
        assert loaded[refresh.id].priority == pytest.approx(refresh.priority, rel=1e-6)
        assert loaded[refresh.id].model_copy(update={"priority": refresh.priority}) == refresh
    assert impacts == []
    assert last_eval == NOW

    first = refreshes[0]
    change = StatusChange(
        refresh_id=first.id,
        video_id=first.video_id,
        from_status=RefreshStatus.proposed,
        to_status=RefreshStatus.applied_detected,
        reason="seen",
        applied_at=NOW.date(),
        applied_source="detected",
    )
    impact = RefreshImpact(
        refresh_id=first.id,
        applied_at=NOW.date(),
        applied_source="detected",
        pre_window_days=14,
        post_window_days=14,
        pre_velocity=100,
        post_velocity=150,
        ratio=1.5,
        verdict="up",
        computed_at=NOW,
    )
    await store.save_run([], eligibility, [change], [impact], None)
    history, impacts, _ = await store.load_history(NOW - timedelta(days=60))
    applied = next(item for item in history if item.refresh.id == first.id)
    assert applied.refresh.status is RefreshStatus.applied_detected
    assert applied.applied_source == "detected"
    assert impacts[0].impact.verdict == "up"
    assert impacts[0].trigger_kind == "news_entity"

    async with session_factory() as other:
        repository = DescriptionRefreshRepository(other, scope)
        second = refreshes[1]
        assert await repository.dismiss(second.id, "not relevant")
        assert not await repository.mark_applied(second.id, NOW.date())


async def test_link_cache_upserts(session_factory) -> None:
    cache = SqlLinkStatusCache(session_factory)
    entry = CachedLink(
        LinkStatus(
            "site.example/a", LinkStatusClass.unverifiable, NOW, http_status=503, error="server error"
        ),
        NOW + timedelta(hours=24),
    )
    await cache.put_many([entry])
    updated = CachedLink(
        LinkStatus("site.example/a", LinkStatusClass.dead, NOW, http_status=503), NOW + timedelta(days=7)
    )
    await cache.put_many([updated])
    loaded = await cache.get_many(["site.example/a", "missing.example"])
    assert list(loaded) == ["site.example/a"]
    assert loaded["site.example/a"].status.status_class is LinkStatusClass.dead
