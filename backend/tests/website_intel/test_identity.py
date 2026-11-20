from typing import Any

from app.core.config import YouTubeOwnershipSettings
from app.domain.web import SourceKind
from app.domain.youtube import ChannelRecord, OwnershipLabel
from app.ports.context import BusinessLookup
from app.ports.crawler import FetchContext
from app.ports.youtube import VideoBatch
from app.services.website_intel.extract.page_builder import PageExtractor
from app.services.website_intel.identity.channel_links import ChannelLinkCollector
from app.services.website_intel.identity.knowledge import KbEntity, KbRelated, pick_entity
from app.services.website_intel.identity.locator import DefaultBusinessLocator
from app.services.website_intel.identity.related_brands import RelatedBrandDetector, brand_name, brand_token
from app.services.website_intel.identity.site_from_channel import SiteFromChannel, urls_in
from app.services.website_intel.inputs.site_resolver import SiteResolver
from app.services.website_intel.models import LinkRegion, NavItem, PageLink
from app.services.youtube.ownership import OwnershipVerifier
from app.services.youtube.resolve import ChannelResolver
from tests.website_intel.conftest import NOW, FakeRawFetcher, RecordingReporter, Served, html_page, make_page

APOLLO_ID = "UC0InVdvqNyNzKBl1-TL348A"
IMPOSTOR_ID = "UCimpostor00000000000000"[:24].ljust(24, "x")
HOME = "https://www.apollohospitals.com/"


def channel(
    channel_id: str, title: str, handle: str, description: str = "", country: str = "IN"
) -> ChannelRecord:
    return ChannelRecord(
        channel_id=channel_id,
        title=title,
        handle=handle,
        description=description,
        country=country,
        default_language="en",
        published_at=NOW,
        uploads_playlist_id=None,
        subscriber_count=1000,
        hidden_subscriber_count=False,
        view_count=10,
        video_count=200,
        thumbnail_url=None,
        metadata_fetched_at=NOW,
        stats_fetched_at=NOW,
    )


APOLLO_CHANNEL = channel(
    APOLLO_ID, "Apollo Hospitals", "@apollohospitalsindia", "Visit https://www.apollohospitals.com"
)
IMPOSTOR = channel(IMPOSTOR_ID, "Apollo247 Coaching", "@apollo247", "Learn maths with us")


class FakeGateway:
    def __init__(self) -> None:
        self.channels = {APOLLO_ID: APOLLO_CHANNEL, IMPOSTOR_ID: IMPOSTOR}
        self.handles = {"apollo247": IMPOSTOR, "apollohospitals": None}
        self.usernames = {"apollohospitalsindia": APOLLO_CHANNEL}
        self.calls: list[str] = []

    async def channels_by_id(self, ids: Any, ctx: Any) -> list[ChannelRecord]:
        self.calls.append("channels_by_id")
        return [self.channels[i] for i in ids if i in self.channels]

    async def channel_by_handle(self, handle: str, ctx: Any) -> ChannelRecord | None:
        self.calls.append("forHandle")
        return self.handles.get(handle.lstrip("@").lower())

    async def channel_by_username(self, username: str, ctx: Any) -> ChannelRecord | None:
        self.calls.append("forUsername")
        return self.usernames.get(username.lower())

    async def videos_by_id(self, ids: Any, ctx: Any) -> VideoBatch:
        self.calls.append("videos")
        return VideoBatch(found=[])


class FakeKnowledge:
    def __init__(self, entities: list[KbEntity]) -> None:
        self.entities = entities

    async def search(self, name: str, *, language: str = "en", limit: int = 5) -> list[KbEntity]:
        return self.entities

    async def summary(self, title: str, *, language: str = "en") -> None:
        return None


def home_html(*, youtube: str | None) -> bytes:
    links = [("/about", "About"), ("https://www.apolloari.com/", "Apollo Research and Innovations")]
    if youtube:
        links.append((youtube, "YouTube"))
    return html_page("Apollo Hospitals", "<p>" + "Hospital care across India. " * 40 + "</p>", links=links)


def locator(
    gateway: FakeGateway, fetcher: FakeRawFetcher, knowledge: FakeKnowledge, clock: Any
) -> DefaultBusinessLocator:
    return DefaultBusinessLocator(
        site_resolver=SiteResolver(fetcher),
        extractor=PageExtractor(),
        knowledge=knowledge,
        channel_resolver=ChannelResolver(gateway, OwnershipVerifier(YouTubeOwnershipSettings())),
        gateway=gateway,
        site_from_channel=SiteFromChannel(fetcher),
        clock=clock,
    )


async def test_website_only_finds_channel_from_site_links_and_verifies(clock: Any) -> None:
    gateway = FakeGateway()
    fetcher = FakeRawFetcher(
        {HOME: Served(200, home_html(youtube="https://www.youtube.com/user/apollohospitalsindia"))}
    )
    result = await locator(gateway, fetcher, FakeKnowledge([]), clock).locate(
        BusinessLookup(website_url="www.apollohospitals.com", country_hint="IN"), RecordingReporter()
    )
    assert result.site is not None and result.site.registrable_domain == "apollohospitals.com"
    assert result.channel_id == APOLLO_ID and result.channel_basis == "website_link"
    assert result.ownership is not None and result.ownership.label is OwnershipLabel.verified
    assert result.channel_resolved_via == "forUsername"
    assert not result.needs_user_confirmation


async def test_impostor_channel_with_similar_name_is_never_verified(clock: Any) -> None:
    gateway = FakeGateway()
    fetcher = FakeRawFetcher({HOME: Served(200, home_html(youtube=None))})
    result = await locator(gateway, fetcher, FakeKnowledge([]), clock).locate(
        BusinessLookup(website_url="www.apollohospitals.com", channel_ref="@Apollo247", country_hint="IN"),
        RecordingReporter(),
    )
    assert result.channel_id == IMPOSTOR_ID
    assert result.ownership is not None and result.ownership.label is not OwnershipLabel.verified
    assert result.needs_user_confirmation


async def test_confirmed_site_linking_channel_by_same_as_is_verified_without_back_link(clock: Any) -> None:
    gateway = FakeGateway()
    silent = APOLLO_CHANNEL.model_copy(update={"description": "Hospital care"})
    gateway.channels[APOLLO_ID] = silent
    gateway.usernames["apollohospitalsindia"] = silent
    same_as = '<script type="application/ld+json">{"@type": "Hospital", "sameAs": ["https://www.youtube.com/user/apollohospitalsindia"]}</script>'
    page = home_html(youtube=None).replace(b"</head>", same_as.encode() + b"</head>")
    fetcher = FakeRawFetcher({HOME: Served(200, page)})
    result = await locator(gateway, fetcher, FakeKnowledge([]), clock).locate(
        BusinessLookup(website_url="www.apollohospitals.com", country_hint="IN"), RecordingReporter()
    )
    assert result.channel_id == APOLLO_ID
    assert result.ownership is not None and result.ownership.label is OwnershipLabel.verified
    assert "confirmed_site_links_channel" in {signal.name for signal in result.ownership.signals}


async def test_name_only_resolves_site_through_knowledge_base(clock: Any) -> None:
    entity = KbEntity(qid="Q4780321", label="Apollo Hospitals", websites=["http://www.apollohospitals.com/"])
    fetcher = FakeRawFetcher(
        {
            "http://www.apollohospitals.com/": Served(
                200,
                home_html(youtube="https://www.youtube.com/user/apollohospitalsindia"),
                final_url="https://www.apollohospitals.com/",
            )
        }
    )
    result = await locator(FakeGateway(), fetcher, FakeKnowledge([entity]), clock).locate(
        BusinessLookup(business_name="Apollo Hospitals"), RecordingReporter()
    )
    assert result.site_basis == "knowledge_base"
    assert result.knowledge_entity is not None and result.knowledge_entity.qid == "Q4780321"
    assert result.channel_id == APOLLO_ID
    assert result.needs_user_confirmation


async def test_channel_only_finds_site_from_description(clock: Any) -> None:
    fetcher = FakeRawFetcher(
        {HOME: Served(200, home_html(youtube="https://www.youtube.com/channel/" + APOLLO_ID))}
    )
    result = await locator(FakeGateway(), fetcher, FakeKnowledge([]), clock).locate(
        BusinessLookup(channel_ref="https://www.youtube.com/channel/" + APOLLO_ID), RecordingReporter()
    )
    assert result.site is not None and result.site_basis == "channel_link"
    assert result.ownership is not None and result.ownership.label is OwnershipLabel.verified


def test_channel_link_scoring_prefers_same_as_and_chrome_links() -> None:
    page = make_page(
        HOME,
        "x",
        json_ld=[{"@type": "Organization", "sameAs": ["https://www.youtube.com/@apollohospitals"]}],
        links=[
            PageLink(url="https://www.youtube.com/@apollohospitals", region=LinkRegion.footer),
            PageLink(url="https://www.youtube.com/@doctorchannel", region=LinkRegion.body),
        ],
        youtube_embeds=["https://www.youtube.com/embed/dQw4w9WgXcQ"],
    )
    signals = ChannelLinkCollector().collect([page])
    scores = {candidate.ref.value.lower(): candidate.score for candidate in signals.candidates}
    assert scores == {"apollohospitals": 9, "doctorchannel": 2}
    assert signals.embedded_video_ids == ["dQw4w9WgXcQ"]


async def test_site_from_channel_needs_repeated_or_channel_mentions() -> None:
    finder = SiteFromChannel(FakeRawFetcher({}))
    ctx = FetchContext(client_id=None, run_id=None)
    found = await finder.candidate("Book at https://clinic.com/book", ["https://instagram.com/x"], ctx)
    assert found is not None and found.domain == "clinic.com" and found.in_channel_description
    assert await finder.candidate("", ["see https://other.com once"], ctx) is None
    assert urls_in("visit www.clinic.com/today.") == ["https://www.clinic.com/today"]


def test_pick_entity_prefers_domain_then_close_name() -> None:
    hospital = KbEntity(qid="Q1", label="Apollo Hospitals", websites=["https://www.apollohospitals.com"])
    tyres = KbEntity(qid="Q2", label="Apollo Tyres", websites=["https://apollotyres.com"])
    assert pick_entity([tyres, hospital], name="Apollo", domain="apollohospitals.com") is hospital
    assert pick_entity([tyres, hospital], name="Apollo Hospitals", domain=None) is hospital
    assert pick_entity([tyres], name="Smile Dental", domain=None) is None


def test_related_brands_from_links_json_ld_and_knowledge_base() -> None:
    home = make_page(
        HOME,
        "x",
        nav=[NavItem(label="Our Brands", url=HOME + "brands", depth=1)],
        links=[
            PageLink(url="https://www.apollopharmacy.in/", anchor="Apollo Pharmacy"),
            PageLink(url="https://www.apollo247.com/", anchor="View Health Records"),
            PageLink(url="https://www.google.com/", anchor="Google"),
        ],
        json_ld=[
            {
                "@type": "Organization",
                "subOrganization": {"name": "Apollo Cradle", "url": "https://apollocradle.com"},
            }
        ],
    )
    kb_page = make_page(
        "https://en.wikipedia.org/wiki/Apollo",
        "Subsidiaries: Apollo Diagnostics",
        source=SourceKind.knowledge_base,
    )
    entity = KbEntity(qid="Q1", label="Apollo Hospitals", subsidiaries=[KbRelated(name="Apollo Diagnostics")])
    signals = RelatedBrandDetector().detect(
        [home],
        business_name="Apollo Hospitals",
        own_domain="apollohospitals.com",
        knowledge=entity,
        knowledge_page=kb_page,
    )
    by_name = {signal.name: signal for signal in signals}
    assert by_name["Apollo Pharmacy"].relation == "sister"
    assert "Apollo247" in by_name and by_name["Apollo Cradle"].relation == "subsidiary"
    assert by_name["Apollo Diagnostics"].relation == "subsidiary"
    assert all(signal.domain != "google.com" for signal in signals)


def test_brand_token_and_names() -> None:
    assert brand_token("Apollo Hospitals", "apollohospitals.com") == "apollo"
    assert brand_token(None, "smiledental.com") == "smiledental"
    assert brand_name("Visit Website", "apolloadluxhospital") == "Apolloadluxhospital"
    assert brand_name("Apollo Pharmacy", "apollopharmacy") == "Apollo Pharmacy"
