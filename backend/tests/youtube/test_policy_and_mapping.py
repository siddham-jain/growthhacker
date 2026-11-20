from datetime import timedelta

import pytest
from pydantic import SecretStr

from app.adapters.youtube import fields
from app.adapters.youtube.data_api_v3 import DataApiV3Gateway
from app.adapters.youtube.errors import (
    CommentsDisabled,
    Forbidden,
    GoogleQuotaExceeded,
    InvalidPageToken,
    InvalidRequest,
    KeyRejected,
    NotFound,
    NoYouTubeChannel,
    RateLimited,
    Transient,
    classify_error,
)
from app.adapters.youtube.fields import PolicyViolation, assert_policy_safe
from app.adapters.youtube.http import ApiRequest, ApiResponse
from app.adapters.youtube.mapping import (
    comments_from_thread,
    parse_duration,
    split_keywords,
)
from app.core.clock import FrozenClock
from app.core.config import YouTubeFreshnessSettings
from app.domain.common import VideoFormat
from app.domain.youtube import QuotaPriority, SearchQuery
from app.services.youtube.comments import redact_comment
from app.services.youtube.context import request_context
from tests.youtube.fixtures import (
    APOLLO,
    CLASSIFIER,
    NOW,
    channel_item,
    channel_record,
    video_id,
    video_item,
    video_record,
)

CTX = request_context(QuotaPriority.interactive)


class RecordingHandler:
    def __init__(self, bodies: dict[str, dict]) -> None:
        self.bodies = bodies
        self.requests: list[ApiRequest] = []

    async def send(self, request: ApiRequest) -> ApiResponse:
        assert_policy_safe(request.params)
        self.requests.append(request)
        return ApiResponse(status=200, body=self.bodies.get(request.endpoint, {}))


def gateway(handler: RecordingHandler) -> DataApiV3Gateway:
    return DataApiV3Gateway(
        handler,
        CLASSIFIER,
        FrozenClock(NOW),
        base_url="https://example.test/youtube/v3",
        freshness=YouTubeFreshnessSettings(),
        counting_regime=lambda _: "2026-11-16-coviews",
    )


async def test_no_request_ever_asks_for_topic_details_or_category_id() -> None:
    ids = [video_id(i) for i in range(60)]
    handler = RecordingHandler(
        {
            "channels.list": {"items": [channel_item()]},
            "videos.list": {"items": [video_item(ids[0])]},
            "playlistItems.list": {"items": []},
            "search.list": {"items": []},
            "commentThreads.list": {"items": []},
            "videos.batchGetStats": {"items": []},
            "playlists.list": {"items": []},
        }
    )
    api = gateway(handler)
    await api.channels_by_id([APOLLO], CTX)
    await api.channel_by_handle("@ApolloHospitalsIndia", CTX)
    await api.channel_by_username("GoogleDevelopers", CTX)
    await api.uploads_page("UU" + APOLLO[2:], None, CTX)
    await api.videos_by_id(ids, CTX)
    await api.videos_by_id(ids[:2], CTX, with_localizations=True)
    await api.video_stats(ids, CTX)
    await api.search(SearchQuery(q="knee replacement cost", type="video"), None, CTX)
    await api.comment_threads(ids[0], 100, CTX)
    await api.playlist_owner("PLabcdefghijklmnop", CTX)
    await api.own_channel_id(SecretStr("token"), CTX)
    assert handler.requests
    for request in handler.requests:
        flat = " ".join(f"{key}={value}" for key, value in request.params.items())
        assert "topicDetails" not in flat, request.endpoint
        assert "categoryId" not in flat, request.endpoint
        assert "topicCategories" not in flat, request.endpoint


async def test_mapped_records_never_carry_category_signals() -> None:
    handler = RecordingHandler({"videos.list": {"items": [video_item(video_id(1))]}})
    batch = await gateway(handler).videos_by_id([video_id(1)], CTX)
    dumped = batch.found[0].model_dump_json()
    assert "categoryId" not in dumped
    assert "topic" not in dumped.lower()


def test_policy_guard_rejects_forbidden_parts_and_fields() -> None:
    with pytest.raises(PolicyViolation):
        assert_policy_safe({"part": "snippet,topicDetails"})
    with pytest.raises(PolicyViolation):
        assert_policy_safe({"part": "snippet", "fields": "items(snippet(categoryId))"})
    for mask in (
        fields.VIDEO_FIELDS,
        fields.VIDEO_FIELDS_LOCALIZED,
        fields.CHANNEL_FIELDS,
        fields.SEARCH_FIELDS,
        fields.COMMENT_FIELDS,
        fields.PLAYLIST_ITEM_FIELDS,
    ):
        assert_policy_safe({"part": fields.VIDEO_PARTS, "fields": mask})


async def test_batches_are_capped_at_fifty_ids() -> None:
    ids = [video_id(i) for i in range(120)]
    handler = RecordingHandler({})
    api = gateway(handler)
    await api.video_stats(ids, CTX)
    await api.videos_by_id(ids, CTX)
    sizes = [len(request.params["id"].split(",")) for request in handler.requests]
    assert sizes == [50, 50, 20, 50, 50, 20]
    assert {request.endpoint for request in handler.requests} == {"videos.batchGetStats", "videos.list"}


async def test_missing_ids_are_reported_not_raised() -> None:
    handler = RecordingHandler({"videos.list": {"items": [video_item(video_id(1))]}})
    batch = await gateway(handler).videos_by_id([video_id(1), video_id(2)], CTX)
    assert [video.video_id for video in batch.found] == [video_id(1)]
    assert batch.missing_ids == [video_id(2)]


async def test_uploads_page_skips_private_items_and_uu_owner_needs_no_call() -> None:
    items = [
        {
            "contentDetails": {"videoId": video_id(1), "videoPublishedAt": "2026-11-19T10:00:00Z"},
            "status": {"privacyStatus": "public"},
        },
        {
            "contentDetails": {"videoId": video_id(2), "videoPublishedAt": "2026-11-18T10:00:00Z"},
            "status": {"privacyStatus": "private"},
        },
    ]
    handler = RecordingHandler({"playlistItems.list": {"items": items, "etag": "abc", "nextPageToken": "t2"}})
    api = gateway(handler)
    page = await api.uploads_page("UU" + APOLLO[2:], None, CTX)
    assert [item.video_id for item in page.items] == [video_id(1)]
    assert page.etag == "abc" and page.next_page_token == "t2"
    calls = len(handler.requests)
    assert await api.playlist_owner("UU" + APOLLO[2:], CTX) == APOLLO
    assert len(handler.requests) == calls


async def test_search_keeps_only_the_requested_kind() -> None:
    items = [
        {"id": {"kind": "youtube#video", "videoId": video_id(1)}, "snippet": {"channelId": APOLLO}},
        {"id": {"kind": "youtube#channel", "channelId": APOLLO}, "snippet": {"channelId": APOLLO}},
        {"id": {"kind": "youtube#playlist", "playlistId": "PL123"}, "snippet": {"channelId": APOLLO}},
    ]
    handler = RecordingHandler({"search.list": {"items": items}})
    page = await gateway(handler).search(SearchQuery(q="knee", type="video"), None, CTX)
    assert [(hit.kind, hit.id) for hit in page.items] == [("video", video_id(1))]
    assert handler.requests[0].params["maxResults"] == "50"


@pytest.mark.parametrize(
    ("raw", "seconds"),
    [
        ("PT45S", 45),
        ("PT3M47S", 227),
        ("PT1H2M3S", 3723),
        ("P1DT1S", 86401),
        ("P0D", None),
        ("", None),
        ("x", None),
    ],
)
def test_parse_duration(raw: str, seconds: int | None) -> None:
    assert parse_duration(raw) == seconds


def test_channel_mapping_handles_hidden_counts_and_quoted_keywords() -> None:
    hidden = channel_record(hidden=True)
    assert hidden.subscriber_count is None and hidden.hidden_subscriber_count
    assert hidden.uploads_playlist_id == "UU" + APOLLO[2:]
    assert split_keywords('"best cardiology hospital" apollo "knee"') == [
        "best cardiology hospital",
        "apollo",
        "knee",
    ]
    assert channel_record(uploads=False).uploads_playlist_id is None


def test_video_mapping_covers_live_upcoming_and_hidden_counts() -> None:
    upcoming = video_record(video_id(1), duration="P0D", width=None, height=None, live="upcoming")
    assert upcoming.duration_s is None and upcoming.format is VideoFormat.upcoming
    start = NOW - timedelta(days=2)
    replay = video_record(
        video_id(2),
        duration="PT1H",
        width=400,
        height=225,
        live_details={"actualStartTime": "2026-11-18T09:30:00Z", "actualEndTime": "2026-11-18T10:30:00Z"},
    )
    assert replay.format is VideoFormat.live and replay.effective_published_at == start
    hidden = video_record(video_id(3), likes=None, comments=None)
    assert hidden.like_count is None and not hidden.likes_visible
    assert hidden.comments_state == "disabled" and hidden.comment_count is None
    assert hidden.has_paid_product_placement is False
    assert hidden.thumbnail_url.endswith("hq.jpg")


def test_comment_threads_keep_owner_replies_and_drop_author_identity() -> None:
    thread = {
        "id": "Ugx1",
        "snippet": {
            "channelId": APOLLO,
            "totalReplyCount": 2,
            "topLevelComment": {
                "id": "Ugx1",
                "snippet": {
                    "textOriginal": "What is the cost? mail me at a.b@example.com or call +91 98765 43210",
                    "likeCount": 7,
                    "publishedAt": "2026-11-19T10:00:00Z",
                    "authorChannelId": {"value": "UCviewer00000000000000000"},
                },
            },
        },
        "replies": {
            "comments": [
                {
                    "id": "Ugx1.r1",
                    "snippet": {
                        "authorChannelId": {"value": APOLLO},
                        "textOriginal": "Please call @Apollo",
                        "likeCount": 1,
                        "publishedAt": "2026-11-19T11:00:00Z",
                    },
                },
                {
                    "id": "Ugx1.r2",
                    "snippet": {
                        "authorChannelId": {"value": "UCother000000000000000000"},
                        "textOriginal": "same question",
                        "publishedAt": "2026-11-19T12:00:00Z",
                    },
                },
            ]
        },
    }
    records = comments_from_thread(
        thread, video_id=video_id(1), rank=0, fetched_at=NOW, redact=redact_comment
    )
    assert [record.comment_id for record in records] == ["Ugx1", "Ugx1.r1"]
    top, owner = records
    assert top.owner_replied and not top.is_channel_owner_reply
    assert owner.is_channel_owner_reply
    assert "example.com" not in top.text and "98765" not in top.text
    assert "[email]" in top.text and "[phone]" in top.text
    assert owner.text == "Please call @[user]"
    assert "author" not in top.model_dump_json().lower()


@pytest.mark.parametrize(
    ("status", "reason", "expected"),
    [
        (403, "quotaExceeded", GoogleQuotaExceeded),
        (403, "rateLimitExceeded", RateLimited),
        (429, None, RateLimited),
        (400, "keyInvalid", KeyRejected),
        (403, "accessNotConfigured", KeyRejected),
        (403, "ipRefererBlocked", KeyRejected),
        (403, "commentsDisabled", CommentsDisabled),
        (403, "channelForbidden", Forbidden),
        (404, "videoNotFound", NotFound),
        (400, "invalidPageToken", InvalidPageToken),
        (401, "youtubeSignupRequired", NoYouTubeChannel),
        (500, "backendError", Transient),
        (503, None, Transient),
        (400, "badRequest", InvalidRequest),
    ],
)
def test_error_classification(status: int, reason: str | None, expected: type) -> None:
    body = {"error": {"message": "x", "errors": [{"reason": reason}] if reason else []}}
    assert type(classify_error(status, body)) is expected
