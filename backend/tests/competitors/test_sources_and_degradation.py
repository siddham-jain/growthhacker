from app.core.config import CompetitorBudgetSettings, CompetitorSettings
from app.domain.competitors import CandidateRole, CandidateStatus, CompetitorRelation
from app.domain.youtube import QuotaBucket, SearchHit
from app.ports.llm import TaskId
from app.ports.quota import QuotaExhausted
from app.services.competitors.context import DiscoveryMeter
from app.services.competitors.model import UserCompetitorInput
from app.services.competitors.service import CompetitorDiscoveryService, DiscoveryDeps
from app.services.competitors.sources.base import SourceDeps
from app.services.competitors.sources.llm_source import LlmCandidate
from app.services.competitors.sources.niche_search_source import NicheSearchCandidateSource, niche_queries
from app.services.competitors.sources.user_source import UserCandidateSource
from app.services.competitors.youtube_access import MeteredYouTube
from tests.competitors.apollo import (
    DOCTOR,
    FORTIS,
    PRACTO,
    apollo_crawler,
    apollo_llm,
    apollo_web_search,
    apollo_youtube,
)
from tests.competitors.conftest import make_context
from tests.competitors.fakes import NOW, FakeLLM, FakeWebSearch, untrusted_entities


async def discover(youtube=None, web=None, llm=None, settings=None, **context):
    youtube = youtube or apollo_youtube()
    deps = DiscoveryDeps(youtube=youtube, web_search=web, crawler=apollo_crawler(), llm=llm)
    ctx = make_context(settings, **context)
    return await CompetitorDiscoveryService(deps, ctx.settings).discover(ctx), youtube


def codes(result):
    return {warning.code for warning in result.warnings}


def test_llm_output_has_no_channel_id_field():
    assert not any("channel" in name for name in LlmCandidate.model_fields)


async def test_web_search_outage_degrades_without_failing():
    result, _ = await discover(web=FakeWebSearch(fail=True), llm=apollo_llm())
    assert "WEB_SEARCH_UNAVAILABLE" in codes(result)
    assert not result.proposal.auto_confirmable
    uncorroborated = [c for c in result.candidates if "uncorroborated" in c.flags]
    assert uncorroborated and all(c.status is not CandidateStatus.excluded for c in uncorroborated)


async def test_llm_outage_falls_back_to_rules_and_blocks_auto_confirm():
    result, _ = await discover(web=apollo_web_search(), llm=FakeLLM(down=True))
    assert "LLM_UNAVAILABLE" in codes(result)
    assert not result.proposal.auto_confirmable
    niche = [c for c in result.candidates if c.sources and {s.kind for s in c.sources} == {"niche_search"}]
    assert niche and all(c.relation is CompetitorRelation.content for c in niche)
    assert all("relation_unclassified" in c.flags for c in niche)


async def test_tight_youtube_budget_is_never_exceeded():
    settings = CompetitorSettings(budget=CompetitorBudgetSettings(youtube_units=12, search_calls=2))
    result, youtube = await discover(web=apollo_web_search(), llm=apollo_llm(), settings=settings)
    assert result.usage.yt_units == youtube.units <= 12
    assert youtube.calls["search"] <= 2
    assert "NICHE_SEARCH_PARTIAL" in codes(result)


async def test_time_budget_returns_partial_results():
    settings = CompetitorSettings()
    ticks = iter([0.0, *[1000.0] * 10_000])
    meter = DiscoveryMeter(settings.budget, settings.max_resolution_searches, monotonic=lambda: next(ticks))
    result, _ = await discover(web=apollo_web_search(), llm=apollo_llm(), settings=settings, meter=meter)
    assert result.partial and "DISCOVERY_TIME_LIMIT" in codes(result)


async def test_quota_exhaustion_skips_niche_search():
    youtube = apollo_youtube()

    async def exhausted(query, page_token, ctx):
        raise QuotaExhausted(bucket=QuotaBucket.search, resets_at=NOW)

    youtube.search = exhausted
    result, _ = await discover(youtube=youtube, web=apollo_web_search(), llm=apollo_llm())
    assert "NICHE_SEARCH_SKIPPED" in codes(result)
    assert set(result.proposal.competitor_channel_ids) >= {FORTIS}


def test_niche_queries_put_questions_first_and_or_combine_keywords():
    assert niche_queries(make_context()) == [
        "knee replacement cost in delhi",
        "best hospital for heart surgery",
        "knee replacement|heart surgery",
        "cancer treatment",
    ]


async def test_niche_search_frequency_threshold():
    youtube = apollo_youtube()
    youtube.searches["cancer treatment"] = [SearchHit(kind="video", id="vx", channel_id=FORTIS, rank=0)]
    ctx = make_context()
    source = NicheSearchCandidateSource(SourceDeps(MeteredYouTube(youtube, ctx), None, None))
    found = {raw.channel_id: raw for raw in await source.generate(ctx)}
    assert FORTIS not in found
    assert found[DOCTOR].query_hits == 3 and found[PRACTO].query_hits == 2


async def test_user_inputs_cover_rejections_videos_and_missing_handles():
    youtube = apollo_youtube()
    inputs = [
        UserCompetitorInput(raw="https://www.instagram.com/fortis"),
        UserCompetitorInput(raw="https://youtu.be/Mcek5LoOMZA", intended_role=CandidateRole.niche_set),
        UserCompetitorInput(raw="@doesnotexist"),
        UserCompetitorInput(raw="https://www.maxhealthcare.in"),
        UserCompetitorInput(raw="Narayana Health"),
    ]
    ctx = make_context(user_inputs=inputs)
    raws = await UserCandidateSource(SourceDeps(MeteredYouTube(youtube, ctx), None, None)).generate(ctx)
    assert [raw.name for raw in raws] == ["Fortis Healthcare", "maxhealthcare", "Narayana Health"]
    assert (
        raws[0].lead_refs[0].evidence[0].name == "user_asserted"
        and raws[0].intended_role is CandidateRole.niche_set
    )
    messages = {warning.code: warning.message for warning in ctx.warnings}
    assert messages["USER_INPUT_REJECTED"] == "Only YouTube channels can be tracked."
    assert messages["USER_LINK_OWNER"].startswith("That video was published by")
    assert messages["USER_CHANNEL_NOT_FOUND"] == "No channel with handle @doesnotexist."


async def test_user_added_channel_lands_where_the_user_put_it():
    inputs = [
        UserCompetitorInput(
            raw=f"https://www.youtube.com/channel/{FORTIS}", intended_role=CandidateRole.niche_set
        )
    ]
    result, _ = await discover(web=apollo_web_search(), llm=apollo_llm(), user_inputs=inputs)
    assert FORTIS in result.proposal.niche_channel_ids
    assert FORTIS not in result.proposal.competitor_channel_ids
    assert len(result.proposal.competitor_channel_ids) == 4


async def test_extraction_and_classification_drop_unverifiable_claims():
    result, _ = await discover(web=apollo_web_search(), llm=apollo_llm())
    assert not any(c.brand_name == "Invented Hospital" for c in result.candidates)
    llm = apollo_llm()
    llm.handlers[TaskId.competitor_relation_classification] = lambda prompt: {
        "entities": [
            {
                "key": key,
                "relation": "direct",
                "geography": [{"country_iso2": "XX", "quote": "Delhi"}],
                "offerings": [{"offering_id": "o99", "verdict": "yes", "quote": "cardiology"}],
            }
            for key, _ in untrusted_entities(prompt)
        ]
    }
    result, _ = await discover(web=apollo_web_search(), llm=llm)
    assert all(not c.business.matched_offerings for c in result.candidates)
    assert all(geo.country_iso2 != "XX" for candidate in result.candidates for geo in candidate.geography)


async def test_concurrent_web_searches_respect_the_tavily_budget():
    web = apollo_web_search()
    settings = CompetitorSettings(budget=CompetitorBudgetSettings(tavily_credits=4))
    result, _ = await discover(web=web, llm=apollo_llm(), settings=settings)
    assert web.credits == result.usage.tavily_credits <= 4
    assert "TAVILY_BUDGET_REACHED" in codes(result)
