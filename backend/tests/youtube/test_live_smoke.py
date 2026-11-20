"""opt-in live checks against the real data api; about 10 main-bucket units, 1 batchGetStats unit, no search."""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text

from app.adapters.youtube.factory import YouTubeDataLayer
from app.core.config import Settings
from app.core.container import build_container
from app.domain.youtube import OwnershipLabel, QuotaPriority
from app.ports.youtube import NotModified
from app.services.youtube.context import request_context
from app.services.youtube.ownership import BusinessHint
from tests.conftest import make_settings

pytestmark = [pytest.mark.live, pytest.mark.db]

APOLLO = "UC0InVdvqNyNzKBl1-TL348A"
APOLLO_247 = "UCdZXd2_JaNA8EBLd4fpJU3A"
APOLLO_HINT = BusinessHint(
    name="Apollo Hospitals",
    website_domain="apollohospitals.com",
    country="IN",
    site_channel_links=["https://www.youtube.com/@ApolloHospitalsIndia"],
)


@pytest.fixture
async def layer(migrated_database: str) -> AsyncIterator[YouTubeDataLayer]:
    api_key = Settings().youtube.api_key
    if api_key is None:
        pytest.skip("YOUTUBE_API_KEY is not configured")
    container = build_container(make_settings(youtube={"api_key": api_key.get_secret_value()}))
    yield container.adapter("youtube_data_layer")
    # the smoke writes real rows; remove them so unit tests see a clean shared cache (ledger rows stay)
    async with container.uow() as uow:
        await uow.session.execute(
            text("DELETE FROM yt_channels WHERE channel_id = ANY(:ids)"), {"ids": [APOLLO, APOLLO_247]}
        )
        await uow.session.execute(
            text("DELETE FROM yt_channel_resolutions WHERE channel_id = ANY(:ids)"),
            {"ids": [APOLLO, APOLLO_247]},
        )
        await uow.session.execute(
            text("DELETE FROM yt_http_etag WHERE request_key LIKE :key"), {"key": f"%{APOLLO[2:]}%"}
        )
        await uow.commit()
    await container.aclose()


async def test_resolves_apollo_and_ingests_one_page(layer: YouTubeDataLayer) -> None:
    ctx = request_context(QuotaPriority.interactive)
    resolution = await layer.resolver.resolve("@ApolloHospitalsIndia", ctx, hint=APOLLO_HINT)
    assert resolution.channel is not None and resolution.channel.channel_id == APOLLO
    assert resolution.ownership is not None and resolution.ownership.label is not OwnershipLabel.rejected

    [result] = await layer.ingest.ensure_baseline([APOLLO], ctx)
    assert result.status == "ok"
    assert 0 < len(result.videos) <= 50
    assert any(video.embed_width and video.embed_height for video in result.videos)
    assert all(video.has_paid_product_placement is not None for video in result.videos)
    assert all("categoryId" not in video.model_dump_json() for video in result.videos)

    stored = await layer.store.channel_videos(APOLLO)
    assert {video.video_id for video in result.videos} <= {row.video_id for row in stored}

    stats = await layer.gateway.video_stats([video.video_id for video in result.videos], ctx)
    assert len(stats.snapshots) == len(result.videos)

    channel = resolution.channel
    first = await layer.gateway.uploads_page(channel.uploads_playlist_id, None, ctx)
    assert not isinstance(first, NotModified) and first.etag
    again = await layer.gateway.uploads_page(channel.uploads_playlist_id, None, ctx, etag=first.etag)
    assert isinstance(again, NotModified)


async def test_apollo247_is_not_verified_as_apollo_hospitals(layer: YouTubeDataLayer) -> None:
    ctx = request_context(QuotaPriority.interactive)
    resolution = await layer.resolver.resolve("@Apollo247", ctx, hint=APOLLO_HINT)
    assert resolution.channel is not None and resolution.channel.channel_id != APOLLO
    assert resolution.ownership is not None
    assert resolution.ownership.label is not OwnershipLabel.verified
    assert resolution.needs_user_confirmation
