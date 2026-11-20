from datetime import timedelta

import pytest

from app.core.clock import FrozenClock
from app.core.config import CompetitorSettings
from app.domain.competitors import CandidateRole, CandidateStatus
from app.domain.youtube import OwnershipLabel
from app.models.website import BusinessProfile as ProfileRow
from app.models.website import BuyerQuestion as BuyerQuestionRow
from app.repositories.shared.youtube import ChannelRepository
from app.services.competitors.drift.monitor import CompetitorDriftMonitor
from app.services.competitors.edits import CompetitorEditService
from app.services.competitors.model import UserCompetitorInput
from app.services.competitors.repository import CompetitorRepository, SqlBrandChannelCache
from app.services.competitors.resolve.resolution_cache import CachedResolution
from app.services.competitors.runner import DiscoveryRequest, DiscoveryRunner
from app.services.competitors.select.validation import SetValidationError
from app.services.competitors.service import CompetitorDiscoveryService, DiscoveryDeps
from tests.competitors.apollo import (
    BUYER_QUESTIONS,
    EXPECTED_COMPETITORS,
    FORTIS,
    MAX,
    PRACTO,
    apollo_crawler,
    apollo_llm,
    apollo_profile,
    apollo_web_search,
    apollo_youtube,
)
from tests.competitors.conftest import make_context
from tests.competitors.fakes import NOW, MemoryLocks
from tests.foundation.factories import create_tenant, insert_row


@pytest.fixture
async def scope(session_factory):
    async with session_factory() as session:
        scope = await create_tenant(session, "apollo")
        for priority, text in enumerate(BUYER_QUESTIONS, start=1):
            values = {"text": text, "normalized_text": text, "priority": priority, "status": "confirmed"}
            await insert_row(session, BuyerQuestionRow.__table__, scope, values)
        await session.commit()
    return scope


def runner(session_factory, cache=None):
    deps = DiscoveryDeps(
        youtube=apollo_youtube(),
        web_search=apollo_web_search(),
        crawler=apollo_crawler(),
        llm=apollo_llm(),
        cache=cache or SqlBrandChannelCache(session_factory),
    )
    return DiscoveryRunner(session_factory, MemoryLocks(), deps, CompetitorSettings(), FrozenClock(NOW))


async def discover(session_factory, scope, trigger="onboarding"):
    request = DiscoveryRequest(scope=scope, trigger=trigger, profile=apollo_profile())
    return await runner(session_factory).run(request)


async def candidates_by_name(session_factory, scope):
    async with session_factory() as session:
        loaded = await CompetitorRepository(session, scope).load_candidates()
    return {candidate.brand_name: candidate for candidate in loaded}


async def test_discovery_persists_candidates_run_usage_and_cache(session_factory, scope):
    result = await discover(session_factory, scope)
    stored = await candidates_by_name(session_factory, scope)
    assert len(stored) == len(result.candidates)
    fortis = stored["Fortis Healthcare"]
    assert fortis.primary_channel_id == FORTIS
    assert fortis.channels[0].ownership.label is OwnershipLabel.verified
    assert {source.kind for source in fortis.sources} == {"llm", "web_search"}
    assert stored["Apollo 24|7"].status is CandidateStatus.excluded
    async with session_factory() as session:
        run = await CompetitorRepository(session, scope).runs.last_completed()
        assert run.status == "completed" and run.yt_units == result.usage.yt_units
    cached = await SqlBrandChannelCache(session_factory).lookup([("domain", "fortishealthcare.com")], NOW)
    assert [entry.channel_id for entry in cached] == [FORTIS]


async def test_user_decisions_survive_rediscovery(session_factory, scope):
    await discover(session_factory, scope)
    stored = await candidates_by_name(session_factory, scope)
    async with session_factory() as session:
        edits = CompetitorEditService(CompetitorRepository(session, scope), CompetitorSettings())
        await edits.confirm(stored["Fortis Healthcare"].id, NOW)
        await edits.reject(stored["Max Healthcare"].id, "too big", NOW)
        await edits.set_role(stored["Practo"].id, CandidateRole.competitor_set, NOW)
        await session.commit()
    result = await discover(session_factory, scope, trigger="scheduled")
    assert MAX not in result.proposal.competitor_channel_ids
    stored = await candidates_by_name(session_factory, scope)
    assert stored["Fortis Healthcare"].status is CandidateStatus.confirmed
    assert stored["Max Healthcare"].status is CandidateStatus.rejected
    assert stored["Max Healthcare"].rejection_reason == "too big"
    assert stored["Practo"].role is CandidateRole.competitor_set


async def test_freeze_validates_and_versions_the_set(session_factory, scope):
    await discover(session_factory, scope)
    stored = await candidates_by_name(session_factory, scope)
    async with session_factory() as session:
        edits = CompetitorEditService(CompetitorRepository(session, scope), CompetitorSettings())
        await edits.confirm(stored["Fortis Healthcare"].id, NOW)
        await edits.confirm(
            stored["Medanta"].id if "Medanta" in stored else stored["Medanta - The Medicity"].id, NOW
        )
        with pytest.raises(SetValidationError) as error:
            await edits.freeze(NOW)
        assert error.value.code == "competitors_out_of_range"
        await edits.confirm(stored["Manipal Hospitals"].id, NOW)
        with pytest.raises(SetValidationError):
            await edits.confirm(stored["Apollo 24|7"].id, NOW)
        first = await edits.freeze(NOW)
        second = await edits.freeze(NOW + timedelta(minutes=1))
        await session.commit()
    assert first.version == 1 and second.version == 2
    assert len(first.competitor_channel_ids) == 3 and PRACTO in first.niche_channel_ids
    async with session_factory() as session:
        active = await CompetitorRepository(session, scope).active_set()
    assert active.version == 2


async def test_auto_confirm_freezes_the_proposal(session_factory, scope):
    result = await discover(session_factory, scope)
    assert result.proposal.auto_confirmable
    async with session_factory() as session:
        edits = CompetitorEditService(CompetitorRepository(session, scope), CompetitorSettings())
        frozen = await edits.auto_confirm(result.proposal.competitor_channel_ids, NOW)
        await session.commit()
    assert set(frozen.competitor_channel_ids) == EXPECTED_COMPETITORS and frozen.confirmed_by == "auto"


async def test_add_competitor_path(session_factory, scope):
    async with session_factory() as session:
        repository = CompetitorRepository(session, scope)
        run_id = await repository.runs.start("manual", None, NOW)
        ctx = make_context(
            workspace_id=scope.workspace_id,
            client_id=scope.client_id,
            discovery_run_id=run_id,
            trigger="manual",
            user_inputs=[UserCompetitorInput(raw=f"https://www.youtube.com/channel/{MAX}")],
        )
        deps = DiscoveryDeps(youtube=apollo_youtube(), llm=apollo_llm())
        added = await CompetitorEditService(repository, CompetitorSettings()).add(
            CompetitorDiscoveryService(deps, ctx.settings), ctx, NOW
        )
        await session.commit()
    assert [row.status for row in added] == ["user_added"] and added[0].decided_by == "user"


async def test_drift_monitor_flags_missing_channel_once(session_factory, scope):
    result = await discover(session_factory, scope)
    async with session_factory() as session:
        edits = CompetitorEditService(CompetitorRepository(session, scope), CompetitorSettings())
        await edits.auto_confirm(result.proposal.competitor_channel_ids, NOW)
        await ChannelRepository(session).upsert_many(
            [
                {
                    "channel_id": FORTIS,
                    "title": "Fortis Healthcare",
                    "handle": "fortishealthcareofficial",
                    "availability": "unavailable",
                    "metadata_fetched_at": NOW,
                    "stats_fetched_at": NOW,
                }
            ]
        )
        monitor = CompetitorDriftMonitor(session, scope, CompetitorSettings())
        events, checked = await monitor.run(NOW, [])
        again, _ = await monitor.run(NOW, [])
        open_events = await CompetitorRepository(session, scope).open_drift_events()
        await session.commit()
    assert checked >= 5
    assert {event.kind for event in events if event.channel_id == FORTIS} == {"channel_missing"}
    assert [event.kind for event in open_events if event.channel_id == FORTIS] == ["channel_missing"]
    assert again


async def test_sql_brand_cache_expires(session_factory):
    cache = SqlBrandChannelCache(session_factory)
    entry = CachedResolution(
        key_type="domain",
        key="example-brand.com",
        channel_id=FORTIS,
        label=OwnershipLabel.verified,
        score=0.9,
        verified_at=NOW,
    )
    await cache.store([entry, entry], ttl_days=30)
    assert await cache.lookup([("domain", "example-brand.com")], NOW + timedelta(days=29))
    assert not await cache.lookup([("domain", "example-brand.com")], NOW + timedelta(days=31))


async def test_rediscovery_job_runs_when_due_and_reports_new_entrants(session_factory, scope):
    from app.services.competitors.drift.rediscovery import RediscoveryJob, RediscoveryPolicy

    async with session_factory() as session:
        await insert_row(
            session,
            ProfileRow.__table__,
            scope,
            {
                "version": 1,
                "status": "confirmed",
                "created_by": "user",
                "profile": apollo_profile().model_dump(mode="json"),
            },
        )
        await session.commit()
    job = RediscoveryJob(runner(session_factory), session_factory, RediscoveryPolicy(CompetitorSettings()))
    first = await job.run(scope, NOW)
    assert first is not None and {source.kind for c in first.candidates for source in c.sources} <= {
        "llm",
        "web_search",
        "niche_search",
        "rediscovery_news",
    }
    assert await job.run(scope, NOW + timedelta(days=5)) is None
    async with session_factory() as session:
        events = await CompetitorRepository(session, scope).open_drift_events()
    assert {event.kind for event in events} == {"new_entrant"}
