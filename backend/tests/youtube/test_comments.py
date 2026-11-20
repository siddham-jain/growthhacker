from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.config import YouTubeCommentSettings
from app.domain.youtube import CommentRecord, QuotaBucket, QuotaPriority
from app.models.youtube_cache import YtComment, YtCommentFetch, YtVideo
from app.ports.quota import QuotaExhausted
from app.ports.youtube import CommentPage
from app.services.youtube.comments import CommentSampler, redact_comment
from app.services.youtube.context import request_context
from tests.youtube.fixtures import (
    CHANNEL_A,
    CHANNEL_B,
    NOW,
    FakeGateway,
    channel_record,
    video_id,
    video_record,
)

pytestmark = pytest.mark.db

CTX = request_context(QuotaPriority.daily_refresh)


def comment(cid: str, vid: str, text: str, *, owner: bool = False) -> CommentRecord:
    return CommentRecord(
        comment_id=cid,
        video_id=vid,
        text=text,
        like_count=2,
        reply_count=1,
        published_at=NOW,
        relevance_rank=0,
        is_channel_owner_reply=owner,
        owner_replied=not owner,
        fetched_at=NOW,
    )


def test_redaction_masks_contacts_mentions_and_links() -> None:
    text = "Call me on 9876543210 or x@y.com, ask @drsharma https://wa.me/123 kitna kharcha?"
    redacted = redact_comment(text)
    assert redacted == "Call me on [phone] or [email], ask @[user] [link] kitna kharcha?"
    assert len(redact_comment("a" * 5000)) == 1500


def test_eligibility_takes_own_candidates_and_top_competitor_videos() -> None:
    sampler = CommentSampler(None, None, YouTubeCommentSettings(top_per_competitor=2), None)
    own = [video_record(video_id(i), channel_id=CHANNEL_A) for i in range(3)]
    rivals = [video_record(video_id(10 + i), channel_id=CHANNEL_B, views=100 * i) for i in range(5)]
    skipped = [
        video_record(video_id(20), channel_id=CHANNEL_A, comments=0),
        video_record(video_id(21), channel_id=CHANNEL_A, comments=None),
        video_record(video_id(22), channel_id=CHANNEL_A, made_for_kids=True),
        video_record(video_id(23), channel_id=CHANNEL_A, published=NOW - timedelta(days=200)),
    ]
    chosen = sampler.eligible(
        [*own, *rivals, *skipped],
        own_channel_id=CHANNEL_A,
        candidate_ids={video_id(10)},
        window_start=NOW - timedelta(days=90),
    )
    assert {video.video_id for video in chosen} == {
        video_id(0),
        video_id(1),
        video_id(2),
        video_id(10),
        video_id(14),
        video_id(13),
    }


async def test_sampling_stores_redacted_text_and_owner_replies(clock, store) -> None:
    videos = [video_record(video_id(i), channel_id=CHANNEL_A) for i in range(3)]
    await store.save_channels([channel_record(CHANNEL_A)])
    await store.save_videos(videos)
    gateway = FakeGateway(clock)
    gateway.comment_pages[video_id(0)] = CommentPage(
        video_id=video_id(0),
        comments_state="enabled",
        comments=[
            comment("Ugx1", video_id(0), "price? mail a@b.com"),
            comment("Ugx1.r1", video_id(0), "DM us", owner=True),
        ],
    )
    gateway.comment_pages[video_id(1)] = CommentPage(
        video_id=video_id(1), comments_state="disabled", comments=[]
    )
    sampler = CommentSampler(gateway, store, YouTubeCommentSettings(), clock)
    report = await sampler.sample(videos, CTX, max_calls=10)
    assert report.calls == 3 and report.disabled == 1 and report.stored == 2

    async with store.uow() as uow:
        rows = (await uow.session.execute(select(YtComment).order_by(YtComment.comment_id))).scalars().all()
        fetches = {
            row.video_id: row.outcome for row in (await uow.session.execute(select(YtCommentFetch))).scalars()
        }
        state = await uow.session.scalar(
            select(YtVideo.comments_state).where(YtVideo.video_id == video_id(1))
        )
        uow.session.expunge_all()
    top, reply = [row for row in rows if row.video_id == video_id(0)]
    assert top.text_redacted == "price? mail [email]" and not top.is_reply
    assert reply.is_reply and reply.is_channel_owner_reply and reply.parent_comment_id == "Ugx1"
    assert fetches == {video_id(0): "ok", video_id(1): "disabled", video_id(2): "empty"}
    assert state == "disabled"
    assert not {"author", "author_channel_id", "author_name"} & set(YtComment.__table__.columns.keys())

    again = await sampler.sample(videos, CTX, max_calls=10)
    assert again.calls == 0 and again.skipped == 3


async def test_sampling_respects_the_call_cap_and_stops_on_quota(clock, store) -> None:
    videos = [video_record(video_id(i), channel_id=CHANNEL_A) for i in range(5)]
    await store.save_channels([channel_record(CHANNEL_A)])
    await store.save_videos(videos)
    gateway = FakeGateway(clock)
    sampler = CommentSampler(gateway, store, YouTubeCommentSettings(), clock)
    assert (await sampler.sample(videos[:3], CTX, max_calls=2)).calls == 2

    async def exhausted(*args):
        raise QuotaExhausted(QuotaBucket.main, NOW)

    gateway.comment_threads = exhausted
    report = await sampler.sample(videos[3:], CTX, max_calls=10)
    assert report.quota_limited and report.stored == 0
