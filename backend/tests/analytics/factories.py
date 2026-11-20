import hashlib
import uuid
from datetime import UTC, datetime, timedelta

from app.domain.common import ChannelRole, VideoFormat
from app.domain.topics import TopicKind, TopicMatch, TopicRef
from app.domain.youtube import ChannelRecord, CommentRecord, ThumbnailRef, VideoRecord, VideoSnapshot
from app.services.analytics.inputs import AnalyticsInputs, ClientContext, ScopedChannel

AS_OF = datetime(2026, 11, 20, 12, tzinfo=UTC)
CLIENT_ID = uuid.UUID("00000000-0000-0000-0000-0000000000c1")


def channel_id(n: int) -> str:
    return f"UC{n:022d}"


def video_id(prefix: str, n: int) -> str:
    return f"{prefix}{n:0{11 - len(prefix)}d}"


def topic_ref_id(n: int) -> str:
    return f"tr_{n:012x}"


def make_channel(
    cid: str, title: str = "Channel", subscribers: int | None = 10_000, **overrides
) -> ChannelRecord:
    values = {
        "channel_id": cid,
        "title": title,
        "handle": f"@{title.lower().replace(' ', '')}",
        "description": "",
        "country": "IN",
        "default_language": "en",
        "published_at": datetime(2015, 1, 1, tzinfo=UTC),
        "uploads_playlist_id": "UU" + cid[2:],
        "subscriber_count": subscribers,
        "hidden_subscriber_count": subscribers is None,
        "view_count": 1_000_000,
        "video_count": 500,
        "thumbnail_url": None,
        "metadata_fetched_at": AS_OF - timedelta(hours=1),
        "stats_fetched_at": AS_OF - timedelta(hours=1),
    }
    return ChannelRecord(**(values | overrides))


def make_video(
    vid: str,
    cid: str,
    published_at: datetime,
    *,
    title: str = "Heart health tips",
    views: int = 1_000,
    likes: int | None = 10,
    comments: int | None = 1,
    duration_s: int | None = 600,
    fmt: VideoFormat = VideoFormat.long_form,
    description: str = "",
    **overrides,
) -> VideoRecord:
    values = {
        "video_id": vid,
        "channel_id": cid,
        "channel_title": "Channel",
        "published_at": published_at,
        "effective_published_at": published_at,
        "title": title,
        "description": description,
        "content_hash": hashlib.sha256(title.encode()).hexdigest()[:16],
        "description_hash": hashlib.sha256(description.encode()).hexdigest()[:16],
        "duration_s": duration_s,
        "format": fmt,
        "format_confidence": "high",
        "view_count": views,
        "like_count": likes,
        "comment_count": comments,
        "comments_state": "enabled" if comments is not None else "disabled",
        "ingest_source": "uploads",
        "metadata_fetched_at": AS_OF - timedelta(hours=1),
        "thumbnails": {
            "high": ThumbnailRef(url=f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg", width=480, height=360)
        },
    }
    return VideoRecord(**(values | overrides))


def make_snapshot(
    vid: str,
    captured_at: datetime,
    views: int,
    likes: int | None = None,
    comments: int | None = None,
    published_at: datetime | None = None,
) -> VideoSnapshot:
    age = (captured_at - published_at).total_seconds() / 3600 if published_at else 0.0
    return VideoSnapshot(
        video_id=vid,
        slot_at=captured_at.replace(minute=0, second=0, microsecond=0),
        captured_at=captured_at,
        age_hours=max(0.0, age),
        view_count=views,
        like_count=likes,
        comment_count=comments,
        source="batch_stats",
        counting_regime="standard",
    )


def snapshots_at_ages(
    vid: str, published_at: datetime, points: list[tuple[float, int]], like_rate: float | None = 0.01
) -> list[VideoSnapshot]:
    return [
        make_snapshot(
            vid,
            published_at + timedelta(days=age),
            views,
            None if like_rate is None else int(views * like_rate),
            0,
            published_at,
        )
        for age, views in points
    ]


def make_topic_ref(
    n: int, label: str, kind: TopicKind = TopicKind.keyword, core_terms: list[str] | None = None
) -> TopicRef:
    return TopicRef(
        id=topic_ref_id(n),
        client_id=CLIENT_ID,
        profile_version=1,
        kind=kind,
        source_item_id=f"src{n}",
        label=label,
        core_entity_terms=core_terms or [],
        fingerprint=f"fp{n}",
    )


def make_match(vid: str, ref: TopicRef, score: float = 0.8, review: bool = False) -> TopicMatch:
    return TopicMatch(
        client_id=CLIENT_ID,
        video_id=vid,
        topic_ref_id=ref.id,
        topic_kind=ref.kind,
        profile_version=1,
        universe_hash="u1",
        prepared_hash="p1",
        engine_version="e1",
        decision="accepted",
        lexical_score=score,
        combined_score=score,
        method="lexical",
        confidence="high",
        review_reasons=["semantic_only"] if review else [],
        explanation="matched",
        matched_at=AS_OF - timedelta(days=1),
    )


def make_comment(
    vid: str, n: int, text: str, *, replies: int = 0, owner_replied: bool = False, owner: bool = False
) -> CommentRecord:
    return CommentRecord(
        comment_id=f"c{vid}{n}",
        video_id=vid,
        text=text,
        like_count=0,
        reply_count=replies,
        published_at=AS_OF - timedelta(days=2),
        relevance_rank=n,
        is_channel_owner_reply=owner,
        owner_replied=owner_replied,
        fetched_at=AS_OF - timedelta(days=1),
    )


def client_context(**overrides) -> ClientContext:
    values = {
        "client_id": str(CLIENT_ID),
        "timezone": "Asia/Kolkata",
        "country": "IN",
        "languages": ["en", "hi"],
        "website_domains": ["apollohospitals.com"],
        "location_terms": ["Chennai", "Hyderabad"],
        "keyword_terms": ["heart", "stroke"],
        "niche": "healthcare",
        "phone_short_codes": ["1066"],
    }
    return ClientContext(**(values | overrides))


def scoped(channel: ChannelRecord, role: ChannelRole, domains: list[str] | None = None) -> ScopedChannel:
    return ScopedChannel(channel=channel, role=role, website_domains=domains or [])


def make_inputs(channels: list[ScopedChannel], videos: list[VideoRecord], **overrides) -> AnalyticsInputs:
    own = next((c.channel.channel_id for c in channels if c.role is ChannelRole.own), None)
    values = {"client": client_context(), "own_channel_id": own, "channels": channels, "videos": videos}
    return AnalyticsInputs(**(values | overrides))
