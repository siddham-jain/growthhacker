"""opt-in live smoke: 2 youtube units (one playlistItems.list, one videos.list), no search, no link probes."""

import hashlib
from datetime import UTC, datetime

import httpx
import pytest

from app.core.config import Settings
from app.domain.common import VideoFormat
from app.domain.profile import BusinessModel, FreshnessSensitivity
from app.domain.youtube import VideoRecord
from app.services.refresh.inputs import RefreshInputs
from app.services.refresh.links.urls import extract_urls, malformation
from tests.refresh.fixtures import CLIENT_ID, RUN_ID, action, engine, metrics, profile

pytestmark = pytest.mark.live

APOLLO_UPLOADS = "UU0InVdvqNyNzKBl1-TL348A"
API = "https://www.googleapis.com/youtube/v3"


def _duration_s(iso: str) -> int:
    total, number = 0, ""
    for char in iso.removeprefix("PT").removeprefix("P"):
        if char.isdigit():
            number += char
            continue
        total += int(number or 0) * {"H": 3600, "M": 60, "S": 1, "D": 86_400, "T": 0}.get(char, 0)
        number = ""
    return total


def _record(item: dict, now: datetime) -> VideoRecord:
    snippet, details, status = item["snippet"], item["contentDetails"], item["status"]
    published = datetime.fromisoformat(snippet["publishedAt"].replace("Z", "+00:00"))
    duration = _duration_s(details.get("duration", "P0D"))
    live = snippet.get("liveBroadcastContent", "none")
    fmt = VideoFormat.upcoming if live == "upcoming" else VideoFormat.long_form
    description = snippet.get("description", "")
    return VideoRecord(
        video_id=item["id"],
        channel_id=snippet["channelId"],
        channel_title=snippet["channelTitle"],
        published_at=published,
        effective_published_at=published,
        live_broadcast_content=live if live in ("none", "live", "upcoming") else "none",
        title=snippet["title"],
        description=description,
        tags=snippet.get("tags", []),
        content_hash=hashlib.sha1((snippet["title"] + description).encode()).hexdigest(),
        description_hash=hashlib.sha1(description.encode()).hexdigest(),
        default_language=snippet.get("defaultLanguage"),
        duration_s=duration,
        format=fmt,
        format_confidence="medium",
        privacy_status=status.get("privacyStatus", "public"),
        view_count=int(item.get("statistics", {}).get("viewCount", 0)),
        ingest_source="uploads",
        metadata_fetched_at=now,
    )


async def test_apollo_public_hygiene_smoke() -> None:
    api_key = Settings().youtube.api_key
    if api_key is None:
        pytest.skip("YOUTUBE_API_KEY is not configured")
    key = api_key.get_secret_value()
    now = datetime.now(UTC)
    async with httpx.AsyncClient(timeout=20) as client:
        playlist = await client.get(
            f"{API}/playlistItems",
            params={"part": "contentDetails", "playlistId": APOLLO_UPLOADS, "maxResults": 50, "key": key},
        )
        playlist.raise_for_status()
        ids = [item["contentDetails"]["videoId"] for item in playlist.json()["items"]]
        videos = await client.get(
            f"{API}/videos",
            params={
                "part": "snippet,contentDetails,statistics,status,localizations",
                "id": ",".join(ids),
                "key": key,
            },
        )
        videos.raise_for_status()
    records = [_record(item, now) for item in videos.json()["items"]]
    assert records
    data = RefreshInputs(
        client_id=CLIENT_ID,
        run_id=RUN_ID,
        as_of=now,
        window_days=30,
        channel_verified=True,
        profile=profile(
            name="Apollo Hospitals",
            domain="apollohospitals.com",
            sensitivity=FreshnessSensitivity.medium,
            business_model=BusinessModel.multi_location_service,
            regulated=True,
            domain_kind="healthcare",
            country="IN",
            actions=[action("book", "https://www.apollohospitals.com/book-doctor-appointment/")],
        ),
        own_videos=records,
        own_metrics=[metrics(record, velocity=max(1.0, record.view_count / 365)) for record in records],
    )
    output = await engine().run(data)
    assert output.recommendation_drafts == []
    assert all(
        op.claim_level.value == "L0_navigational" or op.text.startswith("#")
        for r in output.refreshes
        for op in r.operations
    )
    assert all(refresh.review_required for refresh in output.refreshes)
    nested = [
        record for record in records if any(malformation(url.raw) for url in extract_urls(record.description))
    ]
    if nested:
        assert output.hygiene.malformed_links >= 1
    assert output.hygiene.videos_checked > 0
