from app.core.clock import FrozenClock
from app.core.config import YouTubeOwnershipSettings
from app.domain.youtube import OwnershipLabel, QuotaPriority, SearchHit, SearchPage
from app.services.youtube.context import request_context
from app.services.youtube.ownership import BusinessHint, OwnershipVerifier, registrable_domain
from app.services.youtube.resolve import ChannelResolver, SearchAllowance
from tests.youtube.fixtures import (
    APOLLO,
    APOLLO_247,
    NOW,
    FakeGateway,
    channel_record,
    video_id,
    video_record,
)

CTX = request_context(QuotaPriority.interactive)
APOLLO_CHANNEL = channel_record(
    APOLLO,
    description="Apollo Hospitals is now open every Sunday. Book at https://www.apollohospitals.com/book",
)
APOLLO_247_CHANNEL = channel_record(
    APOLLO_247,
    title="APOLLO COACHING CLASSES SAMBHAJINAGAR",
    handle="@apollo247",
    description="An Academy of Science Since 2010",
    subscribers="31",
    videos=4,
    country=None,
)
SITE = BusinessHint(
    name="Apollo Hospitals",
    website_domain="apollohospitals.com",
    country="IN",
    site_channel_links=["https://www.youtube.com/@ApolloHospitalsIndia"],
)


def verifier() -> OwnershipVerifier:
    return OwnershipVerifier(YouTubeOwnershipSettings())


def resolver(gateway: FakeGateway) -> ChannelResolver:
    return ChannelResolver(gateway, verifier())


def apollo_gateway() -> FakeGateway:
    return FakeGateway(
        FrozenClock(NOW),
        channels=[APOLLO_CHANNEL, APOLLO_247_CHANNEL],
        videos=[video_record(video_id(1))],
        handles={"apollohospitalsindia": APOLLO, "apollo247": APOLLO_247},
        usernames={"apollohospitals": APOLLO},
    )


async def test_apollo_with_two_way_links_is_verified() -> None:
    verdict = await verifier().assess(APOLLO_CHANNEL, SITE)
    assert verdict.label is OwnershipLabel.verified and verdict.method == "link"
    assert {signal.name for signal in verdict.signals} == {"site_links_channel", "channel_links_site"}


async def test_apollo247_is_rejected_even_though_the_name_matches() -> None:
    verdict = await verifier().assess(APOLLO_247_CHANNEL, SITE)
    assert verdict.label is OwnershipLabel.rejected
    names = {signal.name for signal in verdict.signals}
    assert "site_links_other_channel" in names and "few_videos" in names
    assert not any("name" in name for name in names)


async def test_site_link_alone_is_only_likely_and_oauth_is_decisive() -> None:
    one_way = APOLLO_CHANNEL.model_copy(update={"description": "Open every Sunday"})
    assert (await verifier().assess(one_way, SITE)).label is OwnershipLabel.likely
    oauth = await verifier().assess(
        APOLLO_247_CHANNEL, SITE.model_copy(update={"oauth_channel_id": APOLLO_247})
    )
    assert oauth.label is OwnershipLabel.verified and oauth.method == "oauth"


async def test_embed_majority_and_channel_id_links_count() -> None:
    hint = BusinessHint(
        website_domain="apollohospitals.com",
        site_sameas_links=[f"https://www.youtube.com/channel/{APOLLO}"],
        embed_channel_ids=[APOLLO, APOLLO, APOLLO_247],
    )
    verdict = await verifier().assess(APOLLO_CHANNEL, hint)
    assert {"jsonld_sameas", "embed_majority", "channel_links_site"} <= {s.name for s in verdict.signals}
    assert verdict.score == 1.0


def test_registrable_domain_uses_public_suffixes() -> None:
    assert registrable_domain("https://www.apollohospitals.com/x") == "apollohospitals.com"
    assert registrable_domain("delhi.apollohospitals.co.in") == "apollohospitals.co.in"


async def test_resolve_handle_runs_ownership_and_flags_mismatch() -> None:
    gateway = apollo_gateway()
    resolution = await resolver(gateway).resolve("@Apollo247", CTX, hint=SITE)
    assert resolution.channel.channel_id == APOLLO_247 and resolution.method == "handle"
    assert resolution.needs_user_confirmation
    assert "APOLLO COACHING CLASSES" in resolution.message and "apollohospitals.com" in resolution.message
    good = await resolver(gateway).resolve("https://www.youtube.com/@ApolloHospitalsIndia", CTX, hint=SITE)
    assert good.ownership.label is OwnershipLabel.verified and not good.needs_user_confirmation


async def test_resolve_every_input_form() -> None:
    gateway = apollo_gateway()
    for raw, method in [
        (APOLLO, "id"),
        (f"https://youtu.be/{video_id(1)}", "video"),
        ("https://www.youtube.com/playlist?list=UU0InVdvqNyNzKBl1-TL348A", "playlist"),
        ("https://www.youtube.com/user/apollohospitals", "username"),
        ("https://www.youtube.com/c/apollohospitals", "username"),
        ("ApolloHospitalsIndia", "handle"),
    ]:
        resolution = await resolver(gateway).resolve(raw, CTX)
        assert resolution.channel is not None and resolution.channel.channel_id == APOLLO, raw
        assert resolution.method == method, raw
    assert "search.list" not in gateway.calls


async def test_unknown_inputs_ask_for_a_video_link_without_search() -> None:
    gateway = apollo_gateway()
    missing = await resolver(gateway).resolve("@nobodyhere", CTX)
    assert missing.channel is None and "Paste a link to any video" in missing.message
    custom = await resolver(gateway).resolve("https://www.youtube.com/c/OldName", CTX)
    assert custom.channel is None and "/c/" in custom.message
    assert "search.list" not in gateway.calls


async def test_search_is_the_last_resort_and_capped() -> None:
    gateway = apollo_gateway()
    gateway.search_pages["ApolloHospitalsIndiaOld"] = SearchPage(
        query_key="k",
        items=[SearchHit(kind="channel", id=APOLLO_247, channel_id=APOLLO_247, rank=0)],
        fetched_at=NOW,
        expires_at=NOW,
        from_cache=False,
    )
    gateway.search_pages["apollo247"] = SearchPage(
        query_key="k2",
        items=[SearchHit(kind="channel", id=APOLLO_247, channel_id=APOLLO_247, rank=0)],
        fetched_at=NOW,
        expires_at=NOW,
        from_cache=False,
    )
    allowance = SearchAllowance(limit=1)
    miss = await resolver(gateway).resolve(
        "https://www.youtube.com/c/ApolloHospitalsIndiaOld", CTX, search=allowance
    )
    assert miss.channel is None and miss.candidates
    assert gateway.calls.count("search.list") == 1
    again = await resolver(gateway).resolve("https://www.youtube.com/c/Other", CTX, search=allowance)
    assert again.channel is None and gateway.calls.count("search.list") == 1


async def test_search_match_needs_an_exact_custom_url_and_confirmation() -> None:
    gateway = apollo_gateway()
    gateway.handles.pop("apollo247")
    gateway.search_pages["apollo247"] = SearchPage(
        query_key="k",
        items=[SearchHit(kind="channel", id=APOLLO_247, channel_id=APOLLO_247, rank=0)],
        fetched_at=NOW,
        expires_at=NOW,
        from_cache=False,
    )
    resolution = await resolver(gateway).resolve(
        "https://www.youtube.com/c/apollo247", CTX, search=SearchAllowance()
    )
    assert resolution.channel.channel_id == APOLLO_247
    assert resolution.method == "custom_url_search" and resolution.needs_user_confirmation
