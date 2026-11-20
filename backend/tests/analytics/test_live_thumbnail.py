"""opt-in: ocr and pixel traits on one real apollo thumbnail; one playlistItems call (1 quota unit), no search."""

import httpx
import pytest

from app.core.config import Settings
from app.services.analytics.settings import AnalyticsEngineSettings
from app.services.analytics.thumbnails.analyzer import ThumbnailTraitBuilder
from app.services.analytics.thumbnails.fetch import FetchedThumbnail, YtimgImageSource
from app.services.analytics.thumbnails.rapidocr_reader import RapidOcrReader
from app.services.analytics.thumbnails.text import brand_tokens_of
from tests.analytics.factories import AS_OF

pytestmark = pytest.mark.live

APOLLO_UPLOADS = "UU0InVdvqNyNzKBl1-TL348A"
SIZE_ORDER = ("maxres", "standard", "high", "medium")


async def _latest_apollo_thumbnail(http: httpx.AsyncClient, api_key: str) -> tuple[str, str, str]:
    response = await http.get(
        "https://www.googleapis.com/youtube/v3/playlistItems",
        params={"part": "snippet", "playlistId": APOLLO_UPLOADS, "maxResults": 1, "key": api_key},
    )
    response.raise_for_status()
    snippet = response.json()["items"][0]["snippet"]
    thumbnails = snippet["thumbnails"]
    key = next(k for k in SIZE_ORDER if k in thumbnails)
    return snippet["resourceId"]["videoId"], key, thumbnails[key]["url"]


async def test_real_apollo_thumbnail_traits():
    api_key = Settings().youtube.api_key
    if api_key is None:
        pytest.skip("YOUTUBE_API_KEY is not configured")
    async with httpx.AsyncClient(timeout=20) as http:
        video_id, key, url = await _latest_apollo_thumbnail(http, api_key.get_secret_value())
        assert url.startswith("https://i.ytimg.com/")
        data = await YtimgImageSource(http).fetch(url)
    assert data, "thumbnail download failed"
    builder = ThumbnailTraitBuilder(RapidOcrReader(), AnalyticsEngineSettings().thumbnails)
    read = builder.read(FetchedThumbnail(source_key=key, url=url, data=data), devanagari_hint=False)
    assert read is not None
    traits = builder.traits(video_id, read, frozenset(), brand_tokens_of("Apollo Hospitals"), AS_OF)
    print(
        f"\n{video_id} {key} {traits.source_w}x{traits.source_h} text={traits.text_present} "
        f"words={traits.word_count} boxes={traits.text_box_count} script={traits.script_detected} "
        f"brightness={traits.brightness:.2f} ({traits.brightness_band}) contrast={traits.rms_contrast:.2f} "
        f"({traits.contrast_band}) colour={traits.colorfulness:.0f} ({traits.colorfulness_band}) "
        f"texts={[b.text for b in read.page.boxes][:6]}"
    )
    assert traits.ocr_engine == "rapidocr"
    assert 0.0 <= traits.brightness <= 1.0 and traits.rms_contrast >= 0.0
    assert traits.text_present == (traits.text_box_count > 0)
    assert traits.word_count is None or traits.word_count >= 0
