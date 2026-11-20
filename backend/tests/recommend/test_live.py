from dataclasses import replace

import httpx
import pytest

from app.core.config import RecommendNoCopySettings, Settings
from app.services.recommend.context import CompetitorInfo
from app.services.recommend.creators.collabs import CollaborationDetector
from app.services.recommend.text.copying import CopyDetector
from app.services.recommend.writer import TemplateCardRenderer
from tests.recommend.fixtures import CHANNEL_BY_KEY, SPECS, channel_record, video_record

pytestmark = pytest.mark.live


APOLLO_VIDEO = "beZTMveD180"


async def apollo_uploads() -> list[dict]:
    # 3 quota units: videos.list twice + playlistItems.list; the key travels in a header, never the url
    settings = Settings()
    if settings.youtube.api_key is None:
        pytest.skip("YOUTUBE_API_KEY is not configured")
    headers = {"X-Goog-Api-Key": settings.youtube.api_key.get_secret_value()}
    async with httpx.AsyncClient(base_url=settings.youtube.base_url, headers=headers, timeout=15) as http:
        seed = await http.get("/videos", params={"part": "snippet", "id": APOLLO_VIDEO})
        seed.raise_for_status()
        uploads = "UU" + seed.json()["items"][0]["snippet"]["channelId"][2:]
        items = await http.get(
            "/playlistItems", params={"part": "contentDetails", "playlistId": uploads, "maxResults": 10}
        )
        items.raise_for_status()
        ids = ",".join(item["contentDetails"]["videoId"] for item in items.json()["items"])
        videos = await http.get("/videos", params={"part": "snippet,paidProductPlacementDetails", "id": ids})
        videos.raise_for_status()
        return videos.json()["items"]


async def test_real_apollo_uploads_feed_the_copy_and_collaboration_checks(service, inputs) -> None:
    videos = await apollo_uploads()
    titles = [video["snippet"]["title"] for video in videos]

    assert videos and all(
        "hasPaidProductPlacement" in video["paidProductPlacementDetails"] for video in videos
    )
    copy = CopyDetector(titles, [], RecommendNoCopySettings())
    assert copy.check(titles[0]) is not None
    plan = service.plan(inputs, 30)
    for draft in plan.selection.selected:
        assert copy.check(TemplateCardRenderer.summary(draft)) is None

    records = [
        video_record(
            replace(SPECS["a2"], title=video["snippet"]["title"], description=video["snippet"]["description"])
        )
        for video in videos
    ]
    competitor = CompetitorInfo(
        channel_id="UCfortis0000000000000000",
        name="Fortis Healthcare",
        handle="@FortisHealthcare",
        aliases=("Fortis",),
        domain="fortishealthcare.com",
    )
    found = CollaborationDetector(["sponsored by"], ["hospital"]).detect(
        channel_record(CHANNEL_BY_KEY["drheart"]), records, [competitor], []
    )
    assert all(item.matched_token for item in found)
