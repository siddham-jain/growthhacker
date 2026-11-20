"""opt-in live smoke for website intelligence: at most ~20 tavily credits and a handful of youtube units."""

import time
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest

from app.adapters.context.site_direct import SiteDirectSource
from app.adapters.context.site_rendered import SiteRenderedSource
from app.adapters.llm import factory as llm_factory  # noqa: F401
from app.adapters.search import tavily  # noqa: F401
from app.adapters.youtube import factory as youtube_factory  # noqa: F401
from app.core.config import Settings
from app.core.container import Container, build_container
from app.domain.web import BlockReason, CoverageFacet, CrawlBudget, FetchOutcome, FetchResult, SourceKind
from app.domain.youtube import OwnershipLabel
from app.ports.context import BusinessLookup
from app.ports.crawler import FetchContext
from app.services.website_intel.acquisition.toolkit import build_sources
from app.services.website_intel.crawl.crawler import PoliteCrawler
from app.services.website_intel.crawl.politeness import HostGovernor
from app.services.website_intel.crawl.robots_service import RobotsService
from app.services.website_intel.identity.locator import LocatedBusinessDetail
from app.services.website_intel.inputs.site_resolver import SiteResolver
from app.services.website_intel.service import WebsiteIntelService, deps_from_container
from tests.conftest import make_settings
from tests.website_intel.conftest import RecordingReporter
from tests.website_intel.test_cascade import Always403Fetcher

pytestmark = [pytest.mark.live, pytest.mark.db]
MAX_TAVILY_CREDITS = 15
# plan 03 §5.10 output sizes; 10's routing table defaults are smaller (reported as a contract change)
LLM_TASK_OVERRIDES = {
    "website_page_selection": {"max_output_tokens": 2048},
    "website_page_map": {"max_output_tokens": 4096},
    "website_keyword_enrichment": {"max_output_tokens": 12_000, "timeout_s": 120},
    "website_freshness_profile": {"max_output_tokens": 8_000, "timeout_s": 90},
}


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
            llm={
                "anthropic": {"api_key": keys["anthropic"].get_secret_value()},
                "tasks": LLM_TASK_OVERRIDES,
                "budgets": {"per_call_max_usd": 0.30},
            },
        )
    )
    yield container
    await container.aclose()


class BlockedRenderer:
    name = "render"

    async def render(self, url: str, ctx: Any) -> FetchResult:
        return FetchResult(
            requested_url=url,
            final_url=url,
            source_kind=SourceKind.site_rendered,
            outcome=FetchOutcome.blocked,
            status_code=403,
            block_reason=BlockReason.waf_block,
            fetched_at=datetime.now(UTC),
        )


async def test_live_apollo_profile_survives_forced_site_failure(container: Container) -> None:
    service = WebsiteIntelService(deps_from_container(container))
    client_id = uuid.uuid4()
    site = (
        await SiteResolver(service._fetcher).resolve(
            "apollohospitals.com", FetchContext(client_id=None, run_id=None)
        )
    ).site
    assert site is not None
    located = LocatedBusinessDetail(
        business_name="Apollo Hospitals",
        site=site,
        site_basis="user_input",
        channel_id=None,
        channel_basis=None,
        needs_user_confirmation=False,
    )
    llm = service.llm(client_id=client_id, run_id=None, stage="crawl_site")
    kit = service.toolkit(client_id=client_id, run_id=None, llm=llm, country_hint="IN")
    waf = Always403Fetcher()
    forced = [
        SiteDirectSource(
            PoliteCrawler(
                fetcher=waf,
                robots=RobotsService(waf, token="GrowthHackerBot"),
                governor=HostGovernor(max_concurrency=1, min_delay_s=0.0),
                extractor=kit.extractor,
                settings=kit.profile,
                clock=kit.clock,
            ),
            kit,
        ),
        SiteRenderedSource(BlockedRenderer(), None, kit),
    ]
    others = [
        source
        for source in build_sources(kit)
        if source.source_kind not in {SourceKind.site_direct, SourceKind.site_rendered}
    ]
    reporter = RecordingReporter()
    started = time.monotonic()
    outcome = await service.acquire(
        located,
        reporter,
        client_id=client_id,
        run_id=None,
        country_hint="IN",
        sources=[*forced, *others],
        tavily_credit_cap=MAX_TAVILY_CREDITS,
    )
    kinds = {page.page.source_kind for page in outcome.pages}
    print(
        "tiers",
        [
            (tier.tier, tier.source_kind.value, tier.pages, tier.blocked, tier.credits)
            for tier in outcome.tiers
        ],
    )
    print("credits", outcome.tavily_credits, "seconds", round(time.monotonic() - started, 1))
    assert outcome.tavily_credits <= MAX_TAVILY_CREDITS
    assert SourceKind.site_direct not in kinds and SourceKind.site_rendered not in kinds
    assert kinds & {SourceKind.reader_proxy, SourceKind.search_index, SourceKind.archive}
    assert CoverageFacet.home in outcome.covered
    assert {"crawl.blocked", "crawl.fallback"} <= set(reporter.names())
    result = await service.build(
        client_id=client_id,
        run_id=None,
        located=located,
        outcome=outcome,
        reporter=reporter,
        version=1,
        country_hint="IN",
    )
    profile = result.profile
    print("llm", result.llm_usage)
    print("profile", profile.identity.name, "|", profile.identity.one_liner, "|", profile.identity.industry)
    print("offerings", [offering.name for offering in profile.offerings[:12]])
    print("ctas", [(action.type, action.target) for action in profile.conversion_actions[:6]])
    print("questions", [question.text for question in result.questions[:6]])
    print("freshness", profile.freshness.sensitivity, profile.freshness.score_components)
    print("sources", profile.coverage.sources_used, "quality", profile.quality.label)
    sources = {fact.source_kind for fact in result.facts.facts.values()}
    assert sources - {SourceKind.site_direct, SourceKind.site_rendered}
    assert "apollo" in profile.identity.name.lower()
    assert profile.offerings and profile.conversion_actions
    await service.aclose()


async def test_live_name_only_onboarding_for_apollo(container: Container) -> None:
    service = WebsiteIntelService(deps_from_container(container))
    reporter = RecordingReporter()
    located = await service.locate(
        BusinessLookup(business_name="Apollo Hospitals", country_hint="IN"),
        reporter,
        client_id=uuid.uuid4(),
        run_id=None,
    )
    print(
        "located",
        located.site,
        located.site_basis,
        located.channel_id,
        located.channel_basis,
        located.ownership,
        located.notes,
    )
    assert located.site is not None and located.site.registrable_domain == "apollohospitals.com"
    assert located.site_basis == "knowledge_base"
    assert located.channel_id == "UC0InVdvqNyNzKBl1-TL348A"
    # the site links the channel (json-ld sameAs) but its embeds come from regional apollo channels, so 02's
    # c38 thresholds stop at likely and the user confirms
    assert located.ownership is not None and located.ownership.method == "link"
    assert located.ownership.label in {OwnershipLabel.verified, OwnershipLabel.likely}
    assert located.needs_user_confirmation
    await service.aclose()


async def test_live_apollo_direct_crawl_with_compat_agent(container: Container) -> None:
    service = WebsiteIntelService(deps_from_container(container))
    site = (
        await SiteResolver(service._fetcher).resolve(
            "apollohospitals.com", FetchContext(client_id=None, run_id=None)
        )
    ).site
    assert site is not None
    kit = service.toolkit(
        client_id=None,
        run_id=None,
        llm=service.llm(client_id=None, run_id=None, stage="t"),
        country_hint="IN",
    )
    assert kit.crawler is not None
    started = time.monotonic()
    harvest = await kit.crawler.harvest(
        site, CrawlBudget(max_discovered=4, max_fetches=10), RecordingReporter()
    )
    print("stats", dict(harvest.stats), round(time.monotonic() - started, 1))
    assert harvest.ua_mode.value == "compat"
    assert harvest.stats["sitemap_urls"] > 0 and len(harvest.pages) >= 5
    assert time.monotonic() - started < 90
    await service.aclose()
