from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import regex
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from app.adapters.youtube.errors import Forbidden, NotFound
from app.core.clock import Clock
from app.core.config import YouTubeCommentSettings
from app.core.logging import redact_text
from app.domain.youtube import CommentRecord, VideoRecord
from app.models.youtube_cache import YtCommentFetch, YtVideo
from app.ports.quota import QuotaExhausted
from app.ports.youtube import RequestContext, YouTubeGateway
from app.repositories.shared import CommentRepository
from app.services.youtube.store import YouTubeStore

MENTION = regex.compile(r"(?<![\w@])@[\p{L}\p{N}_.\-·]{2,}")
URL = regex.compile(r"(?i)\bhttps?://\S+")


def redact_comment(text: str, max_chars: int = 1500) -> str:
    """comments are stored without author identity; mentions, links, emails and phone numbers are masked."""
    cleaned = redact_text(text)
    cleaned = URL.sub("[link]", cleaned)
    cleaned = MENTION.sub("@[user]", cleaned)
    return cleaned[:max_chars]


def thread_of(comment_id: str) -> str:
    # reply ids are "<thread id>.<reply id>"
    return comment_id.split(".", 1)[0]


def is_reply(comment_id: str) -> bool:
    return "." in comment_id


@dataclass
class SampleReport:
    calls: int = 0
    stored: int = 0
    skipped: int = 0
    disabled: int = 0
    quota_limited: bool = False
    video_ids: list[str] = field(default_factory=list)


class CommentSampler:
    def __init__(
        self,
        gateway: YouTubeGateway,
        store: YouTubeStore,
        settings: YouTubeCommentSettings,
        clock: Clock,
        *,
        refetch_after: timedelta = timedelta(days=7),
    ) -> None:
        self._gateway = gateway
        self._store = store
        self._settings = settings
        self._clock = clock
        self._refetch_after = refetch_after

    @staticmethod
    def has_comments(video: VideoRecord) -> bool:
        # empty pages still cost a unit, so zero-comment and made-for-kids videos are skipped
        return (
            video.comments_state != "disabled"
            and bool(video.comment_count)
            and not video.made_for_kids
            and video.privacy_status == "public"
        )

    def eligible(
        self,
        videos: Sequence[VideoRecord],
        *,
        own_channel_id: str | None,
        candidate_ids: set[str],
        window_start: datetime,
    ) -> list[VideoRecord]:
        in_window = [video for video in videos if video.effective_published_at >= window_start]
        chosen: dict[str, VideoRecord] = {}
        by_channel: dict[str, list[VideoRecord]] = defaultdict(list)
        for video in in_window:
            if video.channel_id == own_channel_id or video.video_id in candidate_ids:
                chosen[video.video_id] = video
            else:
                by_channel[video.channel_id].append(video)
        for channel_videos in by_channel.values():
            channel_videos.sort(key=lambda video: video.view_count, reverse=True)
            for video in channel_videos[: self._settings.top_per_competitor]:
                chosen.setdefault(video.video_id, video)
        return [video for video in chosen.values() if self.has_comments(video)]

    async def _recently_fetched(self, video_ids: Sequence[str]) -> set[str]:
        cutoff = self._clock.now() - self._refetch_after
        async with self._store.uow() as uow:
            rows = await uow.session.execute(
                select(YtCommentFetch.video_id).where(
                    YtCommentFetch.video_id.in_(list(video_ids)), YtCommentFetch.fetched_at >= cutoff
                )
            )
        return set(rows.scalars())

    async def sample(
        self, videos: Sequence[VideoRecord], ctx: RequestContext, *, max_calls: int
    ) -> SampleReport:
        report = SampleReport()
        recent = await self._recently_fetched([video.video_id for video in videos])
        for video in videos:
            if report.calls >= max_calls:
                break
            if video.video_id in recent or not self.has_comments(video):
                report.skipped += 1
                continue
            report.calls += 1
            try:
                page = await self._gateway.comment_threads(video.video_id, self._settings.max_results, ctx)
            except QuotaExhausted:
                report.quota_limited = True
                break
            except (Forbidden, NotFound):
                await self._save(video.video_id, [], "error")
                continue
            if page.comments_state == "disabled":
                report.disabled += 1
                await self._save(video.video_id, [], "disabled")
                continue
            await self._save(video.video_id, page.comments, "ok" if page.comments else "empty")
            report.stored += len(page.comments)
            report.video_ids.append(video.video_id)
        return report

    async def _save(self, video_id: str, comments: Sequence[CommentRecord], outcome: str) -> None:
        now = self._clock.now()
        rows = [
            {
                "comment_id": comment.comment_id,
                "video_id": video_id,
                "thread_id": thread_of(comment.comment_id),
                "is_reply": is_reply(comment.comment_id),
                "parent_comment_id": thread_of(comment.comment_id) if is_reply(comment.comment_id) else None,
                "text_redacted": redact_comment(comment.text, self._settings.max_chars),
                "like_count": comment.like_count,
                "reply_count": comment.reply_count,
                "relevance_rank": comment.relevance_rank,
                "is_channel_owner_reply": comment.is_channel_owner_reply,
                "published_at": comment.published_at,
                "fetched_at": comment.fetched_at,
            }
            for comment in comments
        ]
        fetch = insert(YtCommentFetch).values(video_id=video_id, fetched_at=now, outcome=outcome, n=len(rows))
        fetch = fetch.on_conflict_do_update(
            index_elements=["video_id"],
            set_={
                "fetched_at": fetch.excluded.fetched_at,
                "outcome": fetch.excluded.outcome,
                "n": fetch.excluded.n,
            },
        )
        async with self._store.uow() as uow:
            if rows:
                await uow.repository(CommentRepository).upsert_many(rows)
            await uow.session.execute(fetch)
            if outcome == "disabled":
                await uow.session.execute(
                    update(YtVideo).where(YtVideo.video_id == video_id).values(comments_state="disabled")
                )
            await uow.commit()
