import re
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from app.domain.common import ChannelRole, DomainModel
from app.domain.competitors import OwnerKind
from app.domain.recommendations import CollaborationEvidence, CreatorSubject, RecommendationDraft
from app.domain.youtube import ChannelRecord
from app.services.recommend.context import RecommendContext
from app.services.recommend.creators.collabs import CollaborationDetector
from app.services.recommend.evidence.index import ABOVE_NORMAL_TIERS, EvidenceVideo
from app.services.recommend.stats import median

CreatorSort = Literal[
    "matching_uploads", "median_outlier_ratio", "median_views_at_age", "last_upload_at", "uploads_90d"
]
RelevanceGroup = Literal["strong", "related"]
CREATOR_SORTS: tuple[CreatorSort, ...] = (
    "matching_uploads",
    "median_outlier_ratio",
    "median_views_at_age",
    "last_upload_at",
    "uploads_90d",
)
EXCLUDED_ROLES = frozenset({ChannelRole.own, ChannelRole.competitor, ChannelRole.benchmark})


@dataclass(frozen=True, slots=True)
class CreatorStats:
    # separate simple stats per creator; deliberately never combined into one score
    channel: ChannelRecord
    uploads: tuple[EvidenceVideo, ...]
    matching: tuple[EvidenceVideo, ...]
    matching_in_window: tuple[EvidenceVideo, ...]
    upload_times: tuple[datetime, ...]
    topics_covered: frozenset[str]
    collaborations: tuple[CollaborationEvidence, ...]
    competitor_uploads_checked: int

    @property
    def match_share(self) -> float:
        return len(self.matching) / len(self.uploads) if self.uploads else 0.0

    @property
    def median_ratio(self) -> float | None:
        return median(video.ratio for video in self.matching)

    @property
    def median_views(self) -> int | None:
        value = median(video.views_at_age.views or 0 for video in self.matching)
        return round(value) if value is not None else None

    @property
    def gaps_days(self) -> list[float]:
        ordered = sorted(self.upload_times)
        return [
            (later - earlier).total_seconds() / 86_400
            for earlier, later in zip(ordered, ordered[1:], strict=False)
        ]

    @property
    def last_upload_at(self) -> datetime | None:
        return max(self.upload_times) if self.upload_times else None


class CreatorListEntry(DomainModel):
    channel_id: str
    recommendation_id: str
    group: RelevanceGroup
    sort_value: float | str | None


class CreatorList(DomainModel):
    # grouped by relevance and sorted by one transparent criterion the user picks; no composite score
    window_days: int
    sort: CreatorSort
    available_sorts: list[CreatorSort] = list(CREATOR_SORTS)
    groups: dict[RelevanceGroup, list[CreatorListEntry]]


# a hospital, society or shop is a rival or an institution, not a creator to work with
INSTITUTION_TOKENS = frozenset(
    {
        "hospital",
        "hospitals",
        "clinic",
        "clinics",
        "institute",
        "university",
        "college",
        "pharmacy",
        "foundation",
        "society",
        "association",
        "conference",
        "store",
        "ltd",
        "limited",
        "pvt",
        "inc",
        "llc",
    }
)


def institutional(title: str) -> bool:
    return any(token.casefold() in INSTITUTION_TOKENS for token in re.findall(r"\w+", title))


class CreatorRanker:
    def __init__(self, detector: CollaborationDetector) -> None:
        self.detector = detector

    def candidates(self, ctx: RecommendContext) -> list[CreatorStats]:
        settings = ctx.settings.creators
        pool = ctx.creators
        blocked = self._blocked_channels(ctx)
        outlier_counts: dict[str, int] = {}
        for video in pool.index_90.outliers:
            if video.role not in EXCLUDED_ROLES:
                outlier_counts[video.channel_id] = outlier_counts.get(video.channel_id, 0) + 1
        channel_ids = (set(pool.niche_search_hits) | set(outlier_counts)) - blocked
        eligible_channels = [
            pool.channels[channel_id]
            for channel_id in channel_ids
            if channel_id in pool.channels and self._allowed(ctx, pool.channels[channel_id])
        ]
        prefiltered = sorted(
            eligible_channels,
            key=lambda channel: (
                -outlier_counts.get(channel.channel_id, 0),
                -pool.niche_search_hits.get(channel.channel_id, 0),
                channel.channel_id,
            ),
        )[: settings.prefilter]
        stats = [self._stats(ctx, channel) for channel in prefiltered]
        return [item for item in stats if item is not None]

    def _blocked_channels(self, ctx: RecommendContext) -> set[str]:
        blocked = set(ctx.competitor_ids)
        if ctx.own_channel_id:
            blocked.add(ctx.own_channel_id)
        for candidate in ctx.creators.candidates:
            if candidate.owner_kind is OwnerKind.organisation:
                blocked.update(channel.channel_id for channel in candidate.channels)
        until = {item.channel_id for item in ctx.config.excluded_creators if item.until > ctx.as_of}
        return blocked | until

    def _allowed(self, ctx: RecommendContext, channel: ChannelRecord) -> bool:
        if institutional(channel.title):
            return False
        description = channel.description.lower()
        own_domains = [domain.lower().removeprefix("www.") for domain in ctx.client.website_domains]
        competitor_domains = [c.domain.lower().removeprefix("www.") for c in ctx.competitors if c.domain]
        if any(domain and domain in description for domain in own_domains + competitor_domains):
            return False
        return not (ctx.settings.creators.exclude_made_for_kids and channel.made_for_kids)

    def _stats(self, ctx: RecommendContext, channel: ChannelRecord) -> CreatorStats | None:
        settings = ctx.settings.creators
        pool = ctx.creators
        records = pool.uploads_by_channel.get(channel.channel_id, ())
        upload_times = tuple(sorted(record.published_at for record in records))
        last = max(upload_times) if upload_times else channel.last_upload_at
        if last is None or ctx.as_of - last > timedelta(days=settings.max_inactive_days):
            return None
        uploads = tuple(
            sorted(
                (video for video in pool.index_90.videos if video.channel_id == channel.channel_id),
                key=lambda video: video.published_at,
                reverse=True,
            )[: settings.uploads_cap]
        )
        # a broad keyword ("health") matches almost every upload, so it says nothing about relevance
        broad = {
            topic.id
            for topic in ctx.catalog.all()
            if pool.index_90.breadth(topic.id) > ctx.settings.broad_topic_share
        }
        matching = tuple(video for video in uploads if video.topic_ids - broad)
        in_window = tuple(
            video
            for video in ctx.index.videos
            if video.channel_id == channel.channel_id and video.topic_ids - broad
        )
        competitor_records = [
            record
            for competitor_id in ctx.competitor_ids
            for record in pool.uploads_by_channel.get(competitor_id, ())
        ]
        collaborations = self.detector.detect(channel, records, ctx.competitors, competitor_records)
        return CreatorStats(
            channel=channel,
            uploads=uploads,
            matching=matching,
            matching_in_window=in_window,
            upload_times=upload_times,
            topics_covered=frozenset(topic for video in matching for topic in video.topic_ids - broad),
            collaborations=tuple(collaborations),
            competitor_uploads_checked=len(competitor_records),
        )

    @staticmethod
    def eligible(ctx: RecommendContext, stats: CreatorStats) -> bool:
        settings = ctx.settings.creators
        return stats.match_share >= settings.min_match_share and (
            len(stats.matching_in_window) >= settings.min_matching_uploads
        )


def relevance_group(subject: CreatorSubject, strong_share: float) -> RelevanceGroup:
    return "strong" if subject.relevance.match_share >= strong_share else "related"


def sort_value(subject: CreatorSubject, criterion: CreatorSort) -> float | str | None:
    if criterion == "matching_uploads":
        return round(subject.relevance.match_share * subject.uploads_checked)
    if criterion == "median_outlier_ratio":
        return subject.performance.median_outlier_ratio
    if criterion == "median_views_at_age":
        return subject.performance.median_views_at_age
    if criterion == "last_upload_at":
        last = subject.consistency.last_upload_at
        return last.isoformat() if last else None
    return subject.uploads_90d


def order_creators(
    drafts: list[RecommendationDraft], criterion: CreatorSort, strong_share: float
) -> list[RecommendationDraft]:
    # strong relevance group first, then the single chosen metric descending; channel id breaks ties
    def key(draft: RecommendationDraft) -> tuple:
        subject = draft.subject
        assert isinstance(subject, CreatorSubject)
        value = sort_value(subject, criterion)
        group_rank = 0 if relevance_group(subject, strong_share) == "strong" else 1
        if value is None:
            return (group_rank, 1, 0, subject.channel_id)
        numeric = -value if isinstance(value, int | float) else _descending_text(value)
        return (group_rank, 0, numeric, subject.channel_id)

    return sorted(drafts, key=key)


def _descending_text(value: str) -> tuple[int, ...]:
    return tuple(-ord(char) for char in value)


def gap_stats(stats: CreatorStats, min_uploads: int) -> tuple[float | None, float | None]:
    gaps = stats.gaps_days
    if len(stats.upload_times) < min_uploads or not gaps:
        return None, None
    mean = statistics.fmean(gaps)
    cv = statistics.pstdev(gaps) / mean if mean > 0 else None
    return round(statistics.median(gaps), 1), round(cv, 2) if cv is not None else None


def hit_rate(stats: CreatorStats) -> float | None:
    if not stats.uploads:
        return None
    return round(
        sum(1 for video in stats.uploads if video.tier in ABOVE_NORMAL_TIERS) / len(stats.uploads), 3
    )
