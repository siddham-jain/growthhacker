import logging
import statistics
from datetime import datetime, timedelta

from app.domain.topics import TopicRef
from app.domain.trends import CheckStatus, KeywordCandidate, KeywordTrend, TrendVideo
from app.domain.youtube import SearchQuery, VideoRecord
from app.ports.quota import QuotaExhausted
from app.ports.youtube import RequestContext, YouTubeGateway
from app.services.news.text import contains_phrase, tokens
from app.services.trends.candidates import anchor_terms

logger = logging.getLogger(__name__)

MIN_AGE_DAYS = 0.25
MIN_TOKEN_CHARS = 3
FILLER_WORDS = frozenset({"and", "the", "for", "with", "from", "your", "you", "how", "what", "why"})
# heat reads the fastest few videos, since a keyword takes off through a handful of uploads
LEADERS = 5


def _names_term(title: str, title_tokens: set[str], tags: str, term: str) -> bool:
    if contains_phrase(title, term) or contains_phrase(tags, term):
        return True
    words = {word for word in tokens(term) if len(word) >= MIN_TOKEN_CHARS and word not in FILLER_WORDS}
    if not words:
        return False
    # service labels rarely appear whole in titles, so a keyword of three or more words may miss one
    allowed_missing = 1 if len(words) >= 3 else 0
    return len(words - title_tokens) <= allowed_missing


def grounded(record: VideoRecord, candidate: KeywordCandidate, ref: TopicRef | None) -> bool:
    """a hit counts when its title or tags name the keyword or its topic; descriptions carry hashtag spam."""
    title_tokens = set(tokens(record.title))
    tags = "\n".join(record.tags)
    terms = [candidate.keyword, *(anchor_terms(ref) if ref else [candidate.topic_label])]
    return any(_names_term(record.title, title_tokens, tags, term) for term in terms)


def trend_video(record: VideoRecord, now: datetime, competitors: set[str]) -> TrendVideo:
    age_days = max((now - record.published_at).total_seconds() / 86400, MIN_AGE_DAYS)
    return TrendVideo(
        video_id=record.video_id,
        channel_id=record.channel_id,
        channel_title=record.channel_title,
        title=record.title,
        published_at=record.published_at,
        views=record.view_count,
        age_days=round(age_days, 2),
        views_per_day=round(record.view_count / age_days, 1),
        duration_s=record.duration_s,
        thumbnail_url=record.thumbnail_url,
        is_competitor=record.channel_id in competitors,
    )


class KeywordMeasurer:
    """recent uploads for one keyword: one search.list plus one videos.list, both shared-cached by 02."""

    def __init__(self, *, gateway: YouTubeGateway, window_days: int, videos_kept: int) -> None:
        self._gateway = gateway
        self._window_days = window_days
        self._videos_kept = videos_kept

    def _query(self, keyword: str, region: str | None, language: str | None, now: datetime) -> SearchQuery:
        return SearchQuery(
            q=keyword,
            type="video",
            published_after=(now - timedelta(days=self._window_days)).date(),
            order="viewCount",
            region_code=region,
            relevance_language=language,
            max_results=50,
        )

    async def _fetch(
        self, keyword: str, ctx: RequestContext, region: str | None, language: str | None, now: datetime
    ) -> tuple[CheckStatus, list[VideoRecord]]:
        try:
            page = await self._gateway.search(self._query(keyword, region, language, now), None, ctx)
            ids = [hit.id for hit in page.items if hit.kind == "video"]
            batch = await self._gateway.videos_by_id(ids, ctx) if ids else None
        except QuotaExhausted:
            return "unchecked_quota", []
        except Exception:
            logger.exception("trend keyword check failed", extra={"keyword": keyword})
            return "unchecked_error", []
        return ("cached" if page.from_cache else "checked"), list(batch.found) if batch else []

    async def measure(
        self,
        candidate: KeywordCandidate,
        *,
        ref: TopicRef | None,
        ctx: RequestContext,
        region: str | None,
        language: str | None,
        competitors: set[str],
        now: datetime,
    ) -> KeywordTrend:
        status, records = await self._fetch(candidate.keyword, ctx, region, language, now)
        after = now - timedelta(days=self._window_days)
        videos = [
            trend_video(record, now, competitors)
            for record in records
            if record.published_at >= after and grounded(record, candidate, ref)
        ]
        videos.sort(key=lambda v: (-v.views_per_day, v.video_id))
        rates = [v.views_per_day for v in videos]
        leaders = rates[:LEADERS]
        return KeywordTrend(
            candidate=candidate,
            status=status,
            checked_at=now,
            window_days=self._window_days,
            recent_videos=len(videos),
            channels=len({v.channel_id for v in videos}),
            competitor_videos=sum(v.is_competitor for v in videos),
            median_views_per_day=round(statistics.median(rates), 1) if rates else None,
            top_views_per_day=rates[0] if rates else None,
            leaders_views_per_day=round(statistics.median(leaders), 1) if leaders else None,
            videos=videos[: self._videos_kept],
        )
