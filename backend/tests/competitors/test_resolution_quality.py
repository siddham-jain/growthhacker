import httpx

from app.adapters.crawler.http_fetcher import DirectHttpFetcher
from app.core.clock import FrozenClock
from app.core.config import CompetitorSettings
from app.domain.competitors import CandidateSource, CompetitorRelation
from app.domain.web import FetchOutcome
from app.ports.crawler import FetchContext
from app.services.competitors.model import CandidateEntity, ChannelLead
from app.services.competitors.resolve.chain import ResolutionChain
from app.services.competitors.resolve.legacy_and_handle import UsernameHandleResolver
from app.services.competitors.resolve.site_link import refs_from_page
from app.services.competitors.service import resolution_batch
from app.services.competitors.site_fetcher import CompetitorSiteFetcher
from app.services.competitors.verify.verifier import OwnershipVerifier
from app.services.website_intel.crawl.fetching import UserAgents
from app.services.website_intel.crawl.robots_service import RobotsService
from app.services.website_intel.inputs.ssrf import SsrfGuard
from tests.competitors.conftest import make_context
from tests.competitors.fakes import NOW, channel, video

FORTIS_ID = "UCn0ShMNqXI3kQP-Eoaf7olg"
SETTINGS = CompetitorSettings()


def entity(
    name: str, *, domain: str | None = None, relation=CompetitorRelation.direct, flags=()
) -> CandidateEntity:
    return CandidateEntity(
        key=name,
        brand_name=name,
        normalized_name=name.casefold(),
        official_domain=domain,
        relation=relation,
        flags=set(flags),
        sources=[CandidateSource(kind="llm", ref="profile", retrieved_at=NOW)],
    )


def fortis_lead(description: str) -> ChannelLead:
    record = channel(FORTIS_ID, "Fortis Healthcare", handle="@fortishealthcareofficial")
    uploads = [
        video(f"fortisvid{index:02d}", FORTIS_ID, f"Doc Talk {index}", description) for index in range(10)
    ]
    return ChannelLead(FORTIS_ID, "handle_guess", guessed=True, record=record, uploads=uploads)


def verified(target: CandidateEntity, lead: ChannelLead) -> ChannelLead:
    target.leads[lead.channel_id] = lead
    OwnershipVerifier(SETTINGS).verify_entity(target)
    return lead


def test_brand_hashtags_across_uploads_earn_the_llm_review():
    lead = verified(entity("Fortis Healthcare"), fortis_lead("#urology #fortishealthcare #atfortiswecare"))
    names = {signal.name for signal in lead.verdict.signals}
    assert "uploads_brand_reference" in names and "uploads_link_site" not in names
    assert lead.score >= SETTINGS.ownership.adjudication_min


def test_a_linked_domain_is_not_counted_twice():
    lead = verified(
        entity("Fortis Healthcare", domain="fortishealthcare.com"),
        fortis_lead("Book at https://www.fortishealthcare.com/doctors #fortishealthcare"),
    )
    names = [signal.name for signal in lead.verdict.signals]
    assert "uploads_link_site" in names and "uploads_brand_reference" not in names


def test_uploads_are_checked_for_channels_with_only_a_strong_name_match():
    target = entity("Narayana Health")
    record = channel("UCr5mB2bbUrjEKeWNSYqDpdw", "Narayana Health", handle="@narayanahealth")
    lead = verified(target, ChannelLead(record.channel_id, "handle_guess", guessed=True, record=record))
    assert SETTINGS.ownership.name_similarity_strong <= lead.score < SETTINGS.ownership.uncertain_min
    chain = ResolutionChain.__new__(ResolutionChain)
    chain.settings = SETTINGS
    assert chain._upload_candidates([target]) == [lead]


def test_aggregators_beyond_the_cap_still_get_resolved():
    settings = CompetitorSettings(max_entities_to_resolve=2, resolution_reserve_aggregators=1)
    directs = [entity(f"Hospital {index}") for index in range(3)]
    practo = entity("Practo", relation=CompetitorRelation.aggregator)
    tata = entity("Tata 1mg", relation=CompetitorRelation.aggregator)
    batch = resolution_batch([*directs, practo, tata], settings)
    assert batch == [directs[0], directs[1], practo]


class CountingYouTube:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def by_handle(self, handle: str):
        self.calls.append(handle)
        return None


async def test_weak_business_matches_get_a_single_handle_guess():
    youtube = CountingYouTube()
    resolver = UsernameHandleResolver.__new__(UsernameHandleResolver)
    resolver.youtube = youtube
    ctx = make_context()
    await resolver.resolve(entity("Clearmedi Healthcare", flags=("business_tier_low",)), ctx)
    await resolver.resolve(entity("Narayana Health"), ctx)
    assert youtube.calls == ["clearmedihealthcare", "narayanahealth", "narayanahealthofficial"]


async def public_resolver(host: str, port: int) -> list[str]:
    return ["93.184.216.34"]


def site_fetcher(handler) -> CompetitorSiteFetcher:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    fetcher = DirectHttpFetcher(
        client,
        user_agents=UserAgents(honest="ghbot", compat="ghbot-compat"),
        clock=FrozenClock(NOW),
        guard=SsrfGuard(public_resolver),
    )
    return CompetitorSiteFetcher(fetcher, RobotsService(fetcher, token="ghbot"), FrozenClock(NOW))


async def test_site_fetcher_reads_json_ld_sameas_from_an_oversized_page():
    head = (
        '<html><head><script type="application/ld+json">{"@context":"https://schema.org",'
        '"@type":"Hospital","name":"Narayana Health",'
        '"sameAs":["https://www.youtube.com/user/NarayanaHealth"]}</script></head><body>'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")

        # streamed in chunks like a real response, so the capped prefix keeps the head
        async def chunks():
            yield head.encode()
            for _ in range(20):
                yield b"x" * 10_000

        return httpx.Response(200, headers={"content-type": "text/html"}, content=chunks())

    fetcher = site_fetcher(handler)
    page = await fetcher.fetch_page(
        "https://narayanahealth.org/", FetchContext(client_id=None, run_id=None, max_bytes=50_000)
    )
    assert page.outcome is FetchOutcome.ok
    refs = refs_from_page(page)
    assert [(ref.kind, ref.value, ref.method) for ref in refs] == [
        ("username", "NarayanaHealth", "jsonld_sameas")
    ]


async def test_site_fetcher_respects_robots():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /\n")
        raise AssertionError("a disallowed page must not be fetched")

    page = await site_fetcher(handler).fetch_page(
        "https://fortishealthcare.com/", FetchContext(client_id=None, run_id=None)
    )
    assert page.outcome is FetchOutcome.robots_disallowed
