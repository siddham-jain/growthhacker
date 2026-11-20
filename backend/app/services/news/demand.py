import hashlib
import logging
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Literal, Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

from app.core.config import AnalyticsSettings, NewsSettings, QuotaSettings
from app.core.redis import QuotaCounter
from app.domain.analytics import AgeMethod, ViewsAtAgeSummary
from app.domain.news import DemandCheck, EarlyVideo
from app.domain.youtube import QuotaPriority, SearchQuery, VideoRecord
from app.ports.quota import QuotaExhausted
from app.ports.youtube import RequestContext, YouTubeGateway
from app.services.news.text import contains_phrase, normalize

logger = logging.getLogger(__name__)

NEWS_SEARCH_BUCKET = "search_news_demand"
CROWDED_VIDEOS = 20
ACTIVE_VIDEOS = 5
ACTIVE_CHANNELS = 3
BIG_CHANNEL_MULTIPLE = 10
BIG_CHANNEL_VIDEOS = 5
SEARCH_UNITS = 1
CACHE_REUSE = timedelta(hours=24)

Status = Literal["checked", "unchecked_quota", "unchecked_disabled", "unchecked_error", "local_only"]


@dataclass(frozen=True)
class CandidateVideo:
    video_id: str
    channel_id: str
    channel_title: str
    title: str
    description: str
    published_at: datetime
    views: int
    duration_s: int | None = None
    thumbnail_url: str | None = None

    @classmethod
    def from_record(cls, record: VideoRecord) -> "CandidateVideo":
        return cls(
            video_id=record.video_id,
            channel_id=record.channel_id,
            channel_title=record.channel_title,
            title=record.title,
            description=record.description,
            published_at=record.published_at,
            views=record.view_count,
            duration_s=record.duration_s,
            thumbnail_url=record.thumbnail_url,
        )


@dataclass(frozen=True)
class DemandOutcome:
    check: DemandCheck
    videos: dict[str, CandidateVideo] = field(default_factory=dict)
    from_cache: bool = False


class LocalVideoSearch(Protocol):
    # the shared video cache (02); free, so it always runs first
    async def find(
        self, terms: Sequence[str], published_after: datetime, limit: int
    ) -> list[CandidateVideo]: ...


class ChannelBaselines(Protocol):
    # 06 baseline at the same age; None when the channel has no baseline
    async def views_at_age(self, channel_id: str, age_days: int, as_of: datetime) -> float | None: ...

    async def subscriber_count(self, channel_id: str) -> int | None: ...


class DemandCache(Protocol):
    # newest shared check for the entity taken at or after not_before, from any client
    async def get(self, entity_norm: str, not_before: datetime) -> DemandOutcome | None: ...

    async def put(self, entity_norm: str, day: date, query: str, outcome: DemandOutcome) -> None: ...


class NewsSearchReserve:
    """the news share of the daily search-call bucket (O13: 15 of 100), with a per-client cap."""

    def __init__(self, counter: QuotaCounter, quota: QuotaSettings) -> None:
        self._counter = counter
        self._quota = quota

    def day(self, now: datetime) -> date:
        return now.astimezone(ZoneInfo(self._quota.timezone)).date()

    async def try_reserve(self, client_id: UUID, client_cap: int, now: datetime) -> bool:
        reservation = await self._counter.reserve(
            NEWS_SEARCH_BUCKET,
            self.day(now),
            SEARCH_UNITS,
            limit=self._quota.search_split.news,
            client_id=str(client_id),
            client_cap=client_cap,
        )
        return reservation.ok

    async def refund(self, client_id: UUID, now: datetime) -> None:
        await self._counter.refund(NEWS_SEARCH_BUCKET, self.day(now), SEARCH_UNITS, client_id=str(client_id))


def entity_norm(canonical: str) -> str:
    return hashlib.sha1(normalize(canonical).encode()).hexdigest()


def duration_band(duration_s: int | None) -> str:
    if duration_s is None:
        return "unknown"
    for limit, band in ((60, "<1m"), (240, "1-4m"), (480, "4-8m"), (1200, "8-20m")):
        if duration_s < limit:
            return band
    return "20m+"


def saturation(
    videos: Sequence[EarlyVideo], big_channel_videos: int
) -> Literal["empty", "emerging", "active", "crowded"]:
    channels = len({v.channel_id for v in videos})
    if not videos:
        return "empty"
    if len(videos) >= CROWDED_VIDEOS or big_channel_videos >= BIG_CHANNEL_VIDEOS:
        return "crowded"
    if len(videos) >= ACTIVE_VIDEOS and channels >= ACTIVE_CHANNELS:
        return "active"
    return "emerging"


@dataclass(frozen=True)
class DemandRequest:
    client_id: UUID
    run_id: UUID | None
    canonical: str
    aliases: Sequence[str]
    disambiguator: str | None
    first_published_at: datetime
    region: str | None
    language: str | None
    client_cap: int
    own_subscriber_count: int | None = None
    priority: QuotaPriority = QuotaPriority.daily_refresh

    @property
    def terms(self) -> list[str]:
        return list(dict.fromkeys([self.canonical, *self.aliases]))


class YouTubeDemandChecker:
    """early videos for a grounded entity: local cache, shared daily cache, then one search call."""

    def __init__(
        self,
        *,
        gateway: YouTubeGateway | None,
        local: LocalVideoSearch,
        cache: DemandCache,
        reserve: NewsSearchReserve | None,
        baselines: ChannelBaselines,
        news: NewsSettings,
        analytics: AnalyticsSettings,
    ) -> None:
        self._gateway = gateway
        self._local = local
        self._cache = cache
        self._reserve = reserve
        self._baselines = baselines
        self._news = news
        self._analytics = analytics

    def published_after(self, request: DemandRequest, now: datetime) -> datetime:
        lead = timedelta(hours=self._news.early_video_lead_hours)
        return max(request.first_published_at - lead, now - timedelta(days=7))

    def _grounded(self, video: CandidateVideo, request: DemandRequest) -> bool:
        text = f"{video.title}\n{video.description}"
        if not any(contains_phrase(text, term) for term in request.terms):
            return False
        return request.disambiguator is None or contains_phrase(text, request.disambiguator)

    def _unchecked(self, status: Status, request: DemandRequest, now: datetime) -> DemandOutcome:
        check = DemandCheck(
            status=status,
            entity_query=request.canonical,
            published_after=self.published_after(request, now),
            checked_at=now,
            early_video_count=0,
            channel_count=0,
            outlier_count=0,
            median_views_per_hour=None,
            saturation="empty",
            quota_units_spent=0,
        )
        return DemandOutcome(check)

    async def _early_video(self, video: CandidateVideo, now: datetime) -> EarlyVideo:
        age_hours = max((now - video.published_at).total_seconds() / 3600, 0.0)
        age_days = int(age_hours // 24)
        baseline = await self._baselines.views_at_age(video.channel_id, age_days, now)
        ratio = round(video.views / baseline, 3) if baseline and baseline > 0 else None
        is_outlier = (
            ratio is not None
            and ratio >= self._analytics.outlier_ratio_min
            and video.views >= self._analytics.outlier_view_floor
        )
        return EarlyVideo(
            video_id=video.video_id,
            channel_id=video.channel_id,
            channel_title=video.channel_title,
            published_at=video.published_at,
            age_hours=round(age_hours, 2),
            views=video.views,
            views_per_hour=round(video.views / max(age_hours, 1.0), 2),
            views_at_age=ViewsAtAgeSummary(
                age_days=age_days,
                views=video.views,
                method=AgeMethod.observed,
                label=f"views at {age_hours:.0f}h (observed)",
            ),
            outlier_ratio=ratio,
            is_outlier=is_outlier,
            duration_band=duration_band(video.duration_s),
        )

    async def _big_channel_videos(self, videos: Sequence[EarlyVideo], own_subscribers: int | None) -> int:
        if not own_subscribers or len(videos) < BIG_CHANNEL_VIDEOS:
            return 0
        count = 0
        for video in videos:
            subscribers = await self._baselines.subscriber_count(video.channel_id)
            if subscribers and subscribers > BIG_CHANNEL_MULTIPLE * own_subscribers:
                count += 1
        return count

    async def _summarise(
        self,
        status: Status,
        candidates: Sequence[CandidateVideo],
        request: DemandRequest,
        now: datetime,
        units: int,
    ) -> DemandOutcome:
        after = self.published_after(request, now)
        grounded = [v for v in candidates if v.published_at >= after and self._grounded(v, request)]
        videos = [await self._early_video(v, now) for v in grounded]
        big = await self._big_channel_videos(videos, request.own_subscriber_count)
        check = DemandCheck(
            status=status,
            entity_query=request.canonical,
            published_after=after,
            checked_at=now,
            early_videos=videos,
            early_video_count=len(videos),
            channel_count=len({v.channel_id for v in videos}),
            outlier_count=sum(v.is_outlier for v in videos),
            median_views_per_hour=statistics.median(v.views_per_hour for v in videos) if videos else None,
            saturation=saturation(videos, big),
            quota_units_spent=units,
        )
        return DemandOutcome(check, {v.video_id: v for v in grounded})

    def _search_query(self, request: DemandRequest, now: datetime) -> SearchQuery:
        q = f'"{request.canonical}"'
        if request.disambiguator:
            q = f"{q} {request.disambiguator}"
        return SearchQuery(
            q=q,
            type="video",
            published_after=self.published_after(request, now).date(),
            order="relevance",
            region_code=request.region,
            relevance_language=request.language,
            max_results=50,
        )

    async def check(self, request: DemandRequest, now: datetime) -> DemandOutcome:
        after = self.published_after(request, now)
        local = await self._local.find(request.terms, after, limit=50)
        local_outcome = await self._summarise("local_only", local, request, now, 0)
        if local_outcome.check.early_video_count >= self._news.min_reference_videos:
            return local_outcome
        if self._gateway is None or self._reserve is None or request.client_cap <= 0:
            return self._unchecked("unchecked_disabled", request, now)
        key, day = entity_norm(request.canonical), now.date()
        cached = await self._cache.get(key, now - CACHE_REUSE)
        if cached is not None:
            return DemandOutcome(cached.check, cached.videos, from_cache=True)
        if not await self._reserve.try_reserve(request.client_id, request.client_cap, now):
            return self._unchecked("unchecked_quota", request, now)
        ctx = RequestContext(client_id=request.client_id, run_id=request.run_id, priority=request.priority)
        query = self._search_query(request, now)
        try:
            page = await self._gateway.search(query, None, ctx)
            ids = [hit.id for hit in page.items if hit.kind == "video"]
            batch = await self._gateway.videos_by_id(ids, ctx) if ids else None
        except QuotaExhausted:
            await self._reserve.refund(request.client_id, now)
            return self._unchecked("unchecked_quota", request, now)
        except Exception:
            logger.exception("news demand check failed", extra={"entity": request.canonical})
            return self._unchecked("unchecked_error", request, now)
        records = [CandidateVideo.from_record(r) for r in (batch.found if batch else [])]
        units = SEARCH_UNITS + (1 if ids else 0)
        outcome = await self._summarise("checked", records, request, now, units)
        await self._cache.put(key, day, query.q, outcome)
        return outcome
