from datetime import timedelta
from typing import Any

import pytest
from yt_dlp.utils import DownloadError

from app.adapters.youtube.errors import (
    CircuitOpen,
    Forbidden,
    InvalidPageToken,
    KeyRejected,
    NotFound,
    ScraperFailed,
    Transient,
)
from app.adapters.youtube.fallback import FallbackGateway
from app.adapters.youtube.ytdlp import YtDlpGateway
from app.core.clock import FrozenClock
from app.core.config import YouTubeFallbackSettings, YouTubeFreshnessSettings
from app.domain.common import VideoFormat
from app.domain.youtube import QuotaBucket, QuotaPriority, SearchQuery
from app.ports.quota import QuotaExhausted
from app.services.youtube.context import request_context
from tests.youtube.fixtures import APOLLO, CLASSIFIER, NOW, channel_record, video_id

CTX = request_context(QuotaPriority.interactive)
TS = int((NOW - timedelta(days=3)).timestamp())


class StubExtractor:
    """answers yt-dlp calls by url; a value that is an exception is raised like yt-dlp would."""

    def __init__(self, pages: dict[str, Any]) -> None:
        self.pages = pages
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, url: str, options: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((url, options))
        page = self.pages.get(url)
        if page is None:
            raise DownloadError(f"ERROR: [youtube] {url}: This video is unavailable")
        if isinstance(page, Exception):
            raise page
        return page(options) if callable(page) else page


def gateway(pages: dict[str, Any]) -> tuple[YtDlpGateway, StubExtractor]:
    extractor = StubExtractor(pages)
    return (
        YtDlpGateway(
            CLASSIFIER,
            FrozenClock(NOW),
            settings=YouTubeFallbackSettings(),
            freshness=YouTubeFreshnessSettings(),
            counting_regime=lambda _: "standard",
            extractor=extractor,
        ),
        extractor,
    )


def watch(vid: str, **overrides: Any) -> dict[str, Any]:
    info = {
        "id": vid,
        "channel_id": APOLLO,
        "channel": "Apollo Hospitals",
        "title": f"Knee pain {vid}",
        "description": "Book: apollohospitals.com",
        "tags": ["knee"],
        "timestamp": TS,
        "duration": 2037,
        "view_count": 1200,
        "like_count": 30,
        "comment_count": 4,
        "live_status": "not_live",
        "media_type": "video",
        "availability": "public",
        "age_limit": 0,
        "subtitles": {"en": [{}], "live_chat": [{}]},
        "playable_in_embed": True,
        "thumbnail": f"https://i.ytimg.com/vi/{vid}/maxresdefault.jpg",
        "language": "en",
    }
    return {**info, **overrides}


def url(vid: str) -> str:
    return f"https://www.youtube.com/watch?v={vid}"


async def test_channel_maps_page_fields_and_leaves_totals_unknown():
    about = {
        "channel_id": APOLLO,
        "channel": "Apollo Hospitals",
        "uploader_id": "@apollohospitalsindia",
        "description": "Book at apollohospitals.com",
        "channel_follower_count": 1120000,
        "tags": ["apollo", "cardiology"],
        "thumbnails": [
            {"id": "banner_uncropped", "url": "https://yt3/banner"},
            {"id": "avatar_uncropped", "url": "https://yt3/avatar"},
        ],
    }
    yt, _ = gateway({f"https://www.youtube.com/channel/{APOLLO}/about": about})
    [channel] = await yt.channels_by_id([APOLLO, APOLLO], CTX)
    assert channel.data_source == "yt_dlp"
    assert channel.handle == "@apollohospitalsindia"
    assert channel.subscriber_count == 1120000
    assert channel.uploads_playlist_id == "UU" + APOLLO[2:]
    assert channel.keywords == ["apollo", "cardiology"]
    assert channel.thumbnail_url == "https://yt3/avatar"
    assert (channel.view_count, channel.video_count) == (0, 0)


async def test_unknown_channel_is_dropped():
    yt, _ = gateway(
        {
            f"https://www.youtube.com/channel/{APOLLO}/about": DownloadError(
                "ERROR: [youtube:tab] x: YouTube said: This channel does not exist."
            )
        }
    )
    assert await yt.channels_by_id([APOLLO], CTX) == []


async def test_every_call_enables_the_configured_js_runtimes():
    vid = video_id(10)
    yt, extractor = gateway({url(vid): watch(vid), "ytsearch5:knee pain": {"entries": []}})
    await yt.videos_by_id([vid], CTX)
    await yt.search(SearchQuery(q="knee pain", type="video", max_results=5), None, CTX)
    assert [options["js_runtimes"] for _, options in extractor.calls] == [{"deno": {}, "node": {}}] * 2


async def test_long_form_and_short_are_classified_from_page_shape():
    long_id, short_id = video_id(1), video_id(2)
    yt, extractor = gateway(
        {
            url(long_id): watch(long_id, width=3840, height=2160),
            # no format sizes without a js runtime: media_type alone marks the short as vertical
            url(short_id): watch(short_id, duration=38, media_type="short"),
        }
    )
    batch = await yt.videos_by_id([long_id, short_id, video_id(3)], CTX)
    by_id = {video.video_id: video for video in batch.found}
    assert by_id[long_id].format is VideoFormat.long_form
    assert by_id[short_id].format is VideoFormat.short_form
    assert by_id[short_id].aspect == "vertical"
    assert batch.missing_ids == [video_id(3)]
    video = by_id[long_id]
    assert video.data_source == "yt_dlp"
    assert video.has_captions and video.comments_state == "enabled"
    assert (video.view_count, video.like_count, video.comment_count) == (1200, 30, 4)
    assert all("getcomments" not in options for _, options in extractor.calls)


async def test_live_replay_and_upcoming():
    replay, upcoming = video_id(4), video_id(5)
    start = TS - 3600
    yt, _ = gateway(
        {
            url(replay): watch(replay, live_status="was_live", release_timestamp=start, duration=1800),
            url(upcoming): watch(
                upcoming, live_status="is_upcoming", release_timestamp=TS + 86400, duration=None
            ),
        }
    )
    batch = await yt.videos_by_id([replay, upcoming], CTX)
    by_id = {video.video_id: video for video in batch.found}
    assert by_id[replay].format is VideoFormat.live
    assert by_id[replay].live_actual_end_at - by_id[replay].live_actual_start_at == timedelta(minutes=30)
    assert by_id[upcoming].format is VideoFormat.upcoming
    assert by_id[upcoming].live_broadcast_content == "upcoming"


async def test_uploads_pages_with_scraper_tokens():
    playlist = "UU" + APOLLO[2:]

    def listing(options: dict[str, Any]) -> dict[str, Any]:
        start = options["playliststart"]
        ids = range(start, min(start + 49, 70) + 1)
        return {
            "channel_id": APOLLO,
            "playlist_count": 70,
            "entries": [{"id": video_id(n), "timestamp": TS - n * 86400} for n in ids],
        }

    yt, _ = gateway({f"https://www.youtube.com/playlist?list={playlist}": listing})
    first = await yt.uploads_page(playlist, None, CTX)
    assert len(first.items) == 50 and first.next_page_token == "ytdlp:50"
    second = await yt.uploads_page(playlist, first.next_page_token, CTX)
    assert len(second.items) == 20 and second.next_page_token is None
    with pytest.raises(InvalidPageToken):
        await yt.uploads_page(playlist, "CDIQAA", CTX)


async def test_comments_are_redacted_and_owner_replies_kept():
    vid = video_id(6)
    raw = [
        {
            "id": "c1",
            "parent": "root",
            "text": "call me on 9876543210 @bob",
            "like_count": 5,
            "timestamp": TS,
        },
        {"id": "c1.r1", "parent": "c1", "text": "thanks!", "author_is_uploader": True, "timestamp": TS},
        {"id": "c1.r2", "parent": "c1", "text": "same", "timestamp": TS},
        {"id": "c2", "parent": "root", "text": "price?", "author_id": "UCsomeone", "timestamp": TS},
    ]
    yt, extractor = gateway({url(vid): {"id": vid, "comment_count": 4, "comments": raw}})
    page = await yt.comment_threads(vid, 10, CTX)
    assert page.comments_state == "enabled"
    first, owner, second = page.comments
    assert "9876543210" not in first.text and "@bob" not in first.text
    assert first.owner_replied and first.reply_count == 2
    assert owner.is_channel_owner_reply and owner.comment_id == "c1.r1"
    assert not second.owner_replied
    assert not hasattr(second, "author_id")
    assert extractor.calls[0][1]["getcomments"] is True


async def test_search_maps_video_and_channel_hits():
    yt, _ = gateway(
        {
            "ytsearch5:knee pain": {
                "entries": [{"id": video_id(7), "channel_id": APOLLO}, {"id": video_id(8)}]
            },
            "https://www.youtube.com/results?search_query=knee+pain&sp=EgIQAg%3D%3D": {
                "entries": [{"id": APOLLO, "channel_id": APOLLO}]
            },
        }
    )
    videos = await yt.search(SearchQuery(q="knee pain", type="video", max_results=5), None, CTX)
    assert [(hit.kind, hit.id) for hit in videos.items] == [("video", video_id(7))]
    channels = await yt.search(SearchQuery(q="knee pain", type="channel", max_results=5), None, CTX)
    assert [(hit.kind, hit.id) for hit in channels.items] == [("channel", APOLLO)]


async def test_other_scraper_failures_are_raised():
    vid = video_id(9)
    yt, _ = gateway({url(vid): DownloadError("ERROR: Sign in to confirm you're not a bot")})
    with pytest.raises(ScraperFailed):
        await yt.videos_by_id([vid], CTX)


class Primary:
    def __init__(self, error: Exception | None) -> None:
        self.error = error
        self.calls = 0

    async def channels_by_id(self, ids, ctx):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return [channel_record(APOLLO)]

    async def uploads_page(self, playlist_id, page_token, ctx, etag=None):
        self.calls += 1
        raise AssertionError("scraper tokens must not reach the data api")


class Fallback:
    def __init__(self) -> None:
        self.calls = 0

    async def channels_by_id(self, ids, ctx):
        self.calls += 1
        return [channel_record(APOLLO).model_copy(update={"data_source": "yt_dlp"})]

    async def uploads_page(self, playlist_id, page_token, ctx, etag=None):
        self.calls += 1
        return "scraped"


@pytest.mark.parametrize(
    "error",
    [
        KeyRejected("no youtube api key configured", reason="keyMissing"),
        QuotaExhausted(QuotaBucket.main, NOW, "google"),
        QuotaExhausted(QuotaBucket.main, NOW, "project"),
        QuotaExhausted(QuotaBucket.main, NOW, "redis_down"),
        Transient("boom", status=503),
        CircuitOpen("open"),
    ],
)
async def test_falls_back_when_the_api_is_down(error):
    primary, fallback = Primary(error), Fallback()
    [channel] = await FallbackGateway(primary, fallback, enabled=True).channels_by_id([APOLLO], CTX)
    assert channel.data_source == "yt_dlp" and fallback.calls == 1


@pytest.mark.parametrize("error", [NotFound("gone"), Forbidden("no")])
async def test_api_answers_are_not_retried_on_the_scraper(error):
    primary, fallback = Primary(error), Fallback()
    with pytest.raises(type(error)):
        await FallbackGateway(primary, fallback, enabled=True).channels_by_id([APOLLO], CTX)
    assert fallback.calls == 0


@pytest.mark.parametrize("reason", ["run_cap", "client_cap", "priority", "search_pool"])
async def test_own_quota_caps_are_never_scraped_around(reason):
    primary, fallback = Primary(QuotaExhausted(QuotaBucket.main, NOW, reason)), Fallback()
    with pytest.raises(QuotaExhausted) as raised:
        await FallbackGateway(primary, fallback, enabled=True).channels_by_id([APOLLO], CTX)
    assert raised.value.reason == reason and fallback.calls == 0


async def test_empty_scraped_search_expires_at_once():
    yt, _ = gateway({"ytsearch5:knee pain": {"entries": []}})
    page = await yt.search(SearchQuery(q="knee pain", type="video", max_results=5), None, CTX)
    assert page.items == [] and page.expires_at == page.fetched_at


async def test_disabled_fallback_is_a_passthrough():
    primary, fallback = Primary(QuotaExhausted(QuotaBucket.main, NOW)), Fallback()
    with pytest.raises(QuotaExhausted):
        await FallbackGateway(primary, fallback, enabled=False).channels_by_id([APOLLO], CTX)
    assert fallback.calls == 0
    ok = FallbackGateway(Primary(None), fallback, enabled=False)
    [channel] = await ok.channels_by_id([APOLLO], CTX)
    assert channel.data_source == "data_api"


async def test_scraper_page_tokens_stay_on_the_scraper():
    primary, fallback = Primary(None), Fallback()
    page = await FallbackGateway(primary, fallback, enabled=True).uploads_page("UUx", "ytdlp:50", CTX)
    assert page == "scraped" and primary.calls == 0
