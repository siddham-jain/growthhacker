from app.domain.competitors import CandidateRole, CandidateStatus, CompetitorRelation
from app.domain.youtube import OwnershipLabel
from app.services.competitors.service import CompetitorDiscoveryService, DiscoveryDeps
from tests.competitors.apollo import (
    AIIMS_PREP,
    CLEVELAND,
    DOCTOR,
    EXPECTED_COMPETITORS,
    MAYO,
    MEDANTA,
    PRACTO,
    apollo_crawler,
    apollo_llm,
    apollo_web_search,
    apollo_youtube,
)
from tests.competitors.conftest import make_context
from tests.competitors.fakes import InMemoryBrandCache, RecordingReporter


async def run_apollo(llm=None, **overrides):
    youtube, web, crawler, llm = apollo_youtube(), apollo_web_search(), apollo_crawler(), llm or apollo_llm()
    cache, reporter = InMemoryBrandCache(), RecordingReporter()
    deps = DiscoveryDeps(
        youtube=youtube, web_search=web, crawler=crawler, llm=llm, cache=cache, reporter=reporter
    )
    ctx = make_context(**overrides)
    result = await CompetitorDiscoveryService(deps, ctx.settings).discover(ctx)
    return result, youtube, web, cache, reporter


def by_name(result, name):
    return next(
        candidate
        for candidate in result.candidates
        if name == candidate.brand_name or name in candidate.aliases
    )


async def test_apollo_golden_set():
    result, youtube, web, cache, reporter = await run_apollo()
    proposal = result.proposal

    assert set(proposal.competitor_channel_ids) == EXPECTED_COMPETITORS
    assert PRACTO in proposal.niche_channel_ids
    assert set(proposal.benchmark_channel_ids) == {MAYO, CLEVELAND}
    assert DOCTOR in proposal.creator_seed_channel_ids
    assert proposal.ready and not proposal.contains_content_competitors

    sister = by_name(result, "Apollo 24|7")
    assert sister.relation is CompetitorRelation.subsidiary
    assert sister.status is CandidateStatus.excluded
    assert sister.role is CandidateRole.excluded

    proposed = {
        *proposal.competitor_channel_ids,
        *proposal.niche_channel_ids,
        *proposal.benchmark_channel_ids,
    }
    for candidate in result.candidates:
        for match in candidate.channels:
            if match.channel_id in proposed and match.is_primary:
                assert match.ownership.label in {OwnershipLabel.verified, OwnershipLabel.likely}
    assert AIIMS_PREP not in proposed


async def test_apollo_quota_and_budget_ledger():
    result, youtube, web, _, _ = await run_apollo()
    assert result.usage.yt_units == youtube.units
    assert result.usage.yt_units <= 60
    assert result.usage.search_calls == youtube.calls["search"] <= 9
    assert result.usage.tavily_credits == web.credits <= 20
    channel_searches = [query for query in youtube.search_queries if query.type == "channel"]
    assert [query.q for query in channel_searches] == ["AIIMS"]


async def test_apollo_evidence_details():
    result, *_ = await run_apollo()
    medanta = by_name(result, "Medanta")
    primary = next(match for match in medanta.channels if match.is_primary)
    assert primary.channel_id == MEDANTA
    assert primary.ownership.label is OwnershipLabel.verified
    assert {signal.name for signal in primary.ownership.signals} >= {"site_links_channel", "embed_majority"}
    assert by_name(result, "Narayana Health").business.matched_offerings[0].offering_id == "o1"
    assert len(by_name(result, "Narayana Health").business.matched_offerings) == 1
    assert by_name(result, "AIIMS").status is CandidateStatus.no_channel
    zenith = by_name(result, "Zenith Superspeciality Hospitals")
    assert zenith.status is CandidateStatus.excluded and zenith.rejection_reason == "no_independent_evidence"
    assert by_name(result, "Dr. Reddy's Laboratories").relation is CompetitorRelation.supplier
    assert all(candidate.evidence_summary for candidate in result.candidates if candidate.channels)


async def test_apollo_second_client_reuses_the_resolution_cache():
    first, _, _, cache, _ = await run_apollo()
    youtube, web, crawler, llm = apollo_youtube(), apollo_web_search(), apollo_crawler(), apollo_llm()
    deps = DiscoveryDeps(youtube=youtube, web_search=web, crawler=crawler, llm=llm, cache=cache)
    ctx = make_context()
    second = await CompetitorDiscoveryService(deps, ctx.settings).discover(ctx)
    assert set(second.proposal.competitor_channel_ids) == EXPECTED_COMPETITORS
    assert second.usage.tavily_credits < first.usage.tavily_credits
    assert not any("YouTube channel" in query.query for query in web.queries if "Fortis" in query.query)


async def test_resolution_cap_keeps_the_strongest_web_matches():
    from app.core.config import CompetitorSettings

    result, *_ = await run_apollo(settings=CompetitorSettings(max_entities_to_resolve=5))
    assert set(result.proposal.competitor_channel_ids) == EXPECTED_COMPETITORS
    assert "CANDIDATES_UNCHECKED" in {warning.code for warning in result.warnings}
