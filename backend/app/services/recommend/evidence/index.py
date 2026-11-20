import statistics
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.domain.analytics import (
    TRUE_AGE_METHODS,
    VALUELESS_AGE_METHODS,
    AgeMethod,
    MetricFlag,
    OutlierTier,
    OwnPerformanceReport,
    VideoMetrics,
    VideoTraits,
    ViewsAtAgeSummary,
)
from app.domain.common import ChannelRole, DirectnessLevel, FormatSegment
from app.domain.recommendations import VIEWS_DAY7_AGE, ReferenceVideo
from app.domain.topics import AnswerDirectness, TopicMatch
from app.domain.youtube import ChannelRecord, VideoRecord
from app.services.recommend.evidence.traits import flatten_traits
from app.services.recommend.topics import TopicCatalog

MIN_CHANNEL_BASELINE_N = 5
EXCLUDING_FLAGS = frozenset({MetricFlag.duplicate_of, MetricFlag.tiny_baseline, MetricFlag.unavailable_now})
ABOVE_NORMAL_TIERS = frozenset(
    {OutlierTier.normal, OutlierTier.breakout, OutlierTier.outlier, OutlierTier.strong_outlier}
)
BREAKOUT_TIERS = frozenset({OutlierTier.breakout, OutlierTier.outlier, OutlierTier.strong_outlier})
SECONDS_PER_DAY = 86_400


@dataclass(frozen=True, slots=True)
class EvidenceVideo:
    video_id: str
    channel_id: str
    channel_title: str
    role: ChannelRole
    title: str
    description: str
    published_at: datetime
    age_days: float
    thumbnail_url: str | None
    has_paid_product_placement: bool | None
    views_at_age: ViewsAtAgeSummary
    ratio: float
    is_outlier: bool
    tier: OutlierTier
    baseline_median: float
    baseline_n: int
    segment: FormatSegment
    format_key: str
    length_band: str
    traits: Mapping[str, str]
    topic_ids: frozenset[str]
    review_topic_ids: frozenset[str] = frozenset()
    topic_scores: Mapping[str, float] = field(default_factory=dict)
    directness: Mapping[str, tuple[DirectnessLevel, float]] = field(default_factory=dict)

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"

    @property
    def method(self) -> AgeMethod:
        return self.views_at_age.method

    def has_trait(self, key: str, value: str) -> bool:
        return self.traits.get(key) == value

    def directly_answers(self, topic_id: str, min_score: float) -> bool:
        hit = self.directness.get(topic_id)
        if hit is None:
            return False
        level, score = hit
        return level is DirectnessLevel.direct or score >= min_score

    def to_reference(self) -> ReferenceVideo:
        age = self.views_at_age
        is_true_day7 = age.age_days == VIEWS_DAY7_AGE and age.method in TRUE_AGE_METHODS
        return ReferenceVideo(
            video_id=self.video_id,
            url=self.url,
            title=self.title,
            channel=self.channel_title,
            channel_id=self.channel_id,
            channel_role=self.role,
            published_at=self.published_at,
            views_day7=age.views if is_true_day7 else None,
            views_at_age=age,
            outlier_ratio=round(self.ratio, 2),
            channel_baseline_views_at_age=round(self.baseline_median, 1),
            is_outlier=self.is_outlier,
            format_key=self.format_key,
            length_band=self.length_band,
            matched_topic_ids=sorted(self.topic_ids),
            thumbnail_url=self.thumbnail_url,
            has_paid_product_placement=self.has_paid_product_placement,
        )


@dataclass(frozen=True, slots=True)
class OwnVideo:
    # the client's own uploads; ratio is against the client's own baseline at the same age
    video_id: str
    title: str
    published_at: datetime
    age_days: float
    format_key: str | None
    topic_ids: frozenset[str]
    ratio: float | None
    is_outlier: bool
    traits: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class ClientBaseline:
    views_at_age: float
    n: int


class EvidenceIndex:
    """window-sliced evidence for one client and window; built once per run, read by every recommender."""

    def __init__(
        self,
        *,
        window_days: int,
        as_of: datetime,
        videos: list[EvidenceVideo],
        own_videos: list[OwnVideo],
        excluded: Mapping[str, str],
        client_baselines: Mapping[FormatSegment, ClientBaseline],
        comparison_age_days: int,
        all_titles: list[str],
    ) -> None:
        self.window_days = window_days
        self.as_of = as_of
        self.comparison_age_days = comparison_age_days
        self.videos = sorted(videos, key=lambda video: video.video_id)
        self.own_videos = sorted(
            own_videos, key=lambda video: (video.published_at, video.video_id), reverse=True
        )
        self.excluded = dict(excluded)
        self.client_baselines = dict(client_baselines)
        self.all_titles = all_titles
        self._by_id = {video.video_id: video for video in self.videos}
        self._by_topic: dict[str, list[EvidenceVideo]] = defaultdict(list)
        for video in self.videos:
            for topic_id in sorted(video.topic_ids):
                self._by_topic[topic_id].append(video)

    def get(self, video_id: str) -> EvidenceVideo | None:
        return self._by_id.get(video_id)

    @property
    def outliers(self) -> list[EvidenceVideo]:
        return [video for video in self.videos if video.is_outlier]

    def by_topic(self, topic_id: str) -> list[EvidenceVideo]:
        return list(self._by_topic.get(topic_id, ()))

    def with_traits(self, traits: Mapping[str, str]) -> list[EvidenceVideo]:
        return [video for video in self.videos if all(video.has_trait(k, v) for k, v in traits.items())]

    def breadth(self, topic_id: str) -> float:
        if not self.videos:
            return 0.0
        return len(self._by_topic.get(topic_id, ())) / len(self.videos)

    def channel_ids(self, videos: Iterable[EvidenceVideo] | None = None) -> set[str]:
        return {video.channel_id for video in (self.videos if videos is None else videos)}

    def own_by_topic(self, topic_id: str) -> list[OwnVideo]:
        return [video for video in self.own_videos if topic_id in video.topic_ids]

    def client_baseline(self, segment: FormatSegment | None = None) -> ClientBaseline | None:
        if segment is not None and segment in self.client_baselines:
            return self.client_baselines[segment]
        if FormatSegment.long_form in self.client_baselines:
            return self.client_baselines[FormatSegment.long_form]
        return next(iter(self.client_baselines.values()), None)

    @property
    def has_client_history(self) -> bool:
        return bool(self.own_videos)


class EvidenceIndexBuilder:
    def __init__(self, catalog: TopicCatalog, *, topic_match_min: float, min_ref_age_days: int) -> None:
        self.catalog = catalog
        self.topic_match_min = topic_match_min
        self.min_ref_age_days = min_ref_age_days

    def build(
        self,
        *,
        window_days: int,
        comparison_age_days: int,
        as_of: datetime,
        videos: Iterable[VideoRecord],
        channels: Mapping[str, ChannelRecord],
        roles: Mapping[str, ChannelRole],
        own_channel_id: str | None,
        metrics: Mapping[str, VideoMetrics],
        fallback_metrics: Mapping[str, VideoMetrics],
        traits: Mapping[str, VideoTraits],
        matches: Iterable[TopicMatch],
        directness: Iterable[AnswerDirectness],
        own_report: OwnPerformanceReport | None,
        client_lookback_days: int,
    ) -> EvidenceIndex:
        topics, review_topics, scores = self._topic_maps(matches)
        directness_map = self._directness_map(directness)
        window_start = as_of - timedelta(days=window_days)
        lookback_start = as_of - timedelta(days=client_lookback_days)
        evidence: list[EvidenceVideo] = []
        own: list[OwnVideo] = []
        excluded: dict[str, str] = {}
        titles: list[str] = []
        for record in videos:
            if record.published_at > as_of:
                continue
            age_days = (as_of - record.published_at).total_seconds() / SECONDS_PER_DAY
            video_traits = traits.get(record.video_id)
            flat = flatten_traits(video_traits) if video_traits else {}
            if record.channel_id == own_channel_id:
                if record.published_at >= lookback_start:
                    own.append(
                        self._own_video(
                            record,
                            age_days,
                            metrics.get(record.video_id) or fallback_metrics.get(record.video_id),
                            video_traits,
                            flat,
                            topics,
                        )
                    )
                continue
            titles.append(record.title)
            if not window_start <= record.published_at:
                continue
            reason = self._exclusion(record, age_days, metrics.get(record.video_id), video_traits)
            if reason is not None:
                excluded[record.video_id] = reason
                continue
            metric = metrics[record.video_id]
            assert metric.outlier is not None and video_traits is not None
            channel = channels.get(record.channel_id)
            evidence.append(
                EvidenceVideo(
                    video_id=record.video_id,
                    channel_id=record.channel_id,
                    channel_title=channel.title if channel else record.channel_title,
                    role=roles.get(record.channel_id, ChannelRole.niche),
                    title=record.title,
                    description=record.description,
                    published_at=record.published_at,
                    age_days=age_days,
                    thumbnail_url=record.thumbnail_url,
                    has_paid_product_placement=record.has_paid_product_placement,
                    views_at_age=self._views_at_age(metric),
                    ratio=metric.outlier.ratio,
                    is_outlier=metric.outlier.is_outlier,
                    tier=metric.outlier.tier,
                    baseline_median=metric.outlier.baseline_median,
                    baseline_n=metric.outlier.baseline_n,
                    segment=metric.segment,
                    format_key=video_traits.format_key,
                    length_band=video_traits.length_band,
                    traits=flat,
                    topic_ids=frozenset(topics.get(record.video_id, ())),
                    review_topic_ids=frozenset(review_topics.get(record.video_id, ())),
                    topic_scores=scores.get(record.video_id, {}),
                    directness=directness_map.get(record.video_id, {}),
                )
            )
        return EvidenceIndex(
            window_days=window_days,
            as_of=as_of,
            videos=evidence,
            own_videos=own,
            excluded=excluded,
            client_baselines=self._client_baselines(own_report, comparison_age_days, own, metrics),
            comparison_age_days=comparison_age_days,
            all_titles=titles,
        )

    def _exclusion(
        self, record: VideoRecord, age_days: float, metric: VideoMetrics | None, traits: VideoTraits | None
    ) -> str | None:
        if record.live_broadcast_content != "none":
            return "live_or_upcoming"
        if not record.duration_s:
            return "zero_duration"
        if record.availability != "available":
            return "unavailable"
        if age_days < self.min_ref_age_days:
            return "too_new"
        if metric is None or metric.outlier is None or traits is None:
            return "no_metrics"
        outlier = metric.outlier
        if outlier.method in VALUELESS_AGE_METHODS or outlier.tier is OutlierTier.insufficient:
            return "views_at_age_unknown"
        if outlier.baseline_n < MIN_CHANNEL_BASELINE_N or EXCLUDING_FLAGS & set(metric.flags):
            return "unreliable_baseline"
        return None

    def _own_video(
        self,
        record: VideoRecord,
        age_days: float,
        metric: VideoMetrics | None,
        traits: VideoTraits | None,
        flat: dict[str, str],
        topics: Mapping[str, set[str]],
    ) -> OwnVideo:
        outlier = metric.outlier if metric else None
        usable = outlier is not None and outlier.method not in VALUELESS_AGE_METHODS
        return OwnVideo(
            video_id=record.video_id,
            title=record.title,
            published_at=record.published_at,
            age_days=age_days,
            format_key=traits.format_key if traits else None,
            topic_ids=frozenset(topics.get(record.video_id, ())),
            ratio=outlier.ratio if usable and outlier else None,
            is_outlier=bool(usable and outlier and outlier.is_outlier),
            traits=flat,
        )

    @staticmethod
    def _views_at_age(metric: VideoMetrics) -> ViewsAtAgeSummary:
        outlier = metric.outlier
        assert outlier is not None
        stored = metric.views_at_age.get(outlier.age_days)
        label = stored.label if stored else f"views at day {outlier.age_days} ({outlier.method.value})"
        return ViewsAtAgeSummary(
            age_days=outlier.age_days, views=outlier.value, method=outlier.method, label=label
        )

    def _topic_maps(
        self, matches: Iterable[TopicMatch]
    ) -> tuple[dict[str, set[str]], dict[str, set[str]], dict[str, dict[str, float]]]:
        topics: dict[str, set[str]] = defaultdict(set)
        review: dict[str, set[str]] = defaultdict(set)
        scores: dict[str, dict[str, float]] = defaultdict(dict)
        for match in matches:
            if not match.accepted or match.combined_score < self.topic_match_min:
                continue
            if match.topic_ref_id not in self.catalog:
                continue
            topics[match.video_id].add(match.topic_ref_id)
            scores[match.video_id][match.topic_ref_id] = match.combined_score
            if match.review_flag:
                review[match.video_id].add(match.topic_ref_id)
        return topics, review, scores

    @staticmethod
    def _directness_map(
        directness: Iterable[AnswerDirectness],
    ) -> dict[str, dict[str, tuple[DirectnessLevel, float]]]:
        mapping: dict[str, dict[str, tuple[DirectnessLevel, float]]] = defaultdict(dict)
        for item in directness:
            mapping[item.video_id][item.buyer_question_ref_id] = (item.level, item.score)
        return mapping

    @staticmethod
    def _client_baselines(
        report: OwnPerformanceReport | None,
        comparison_age_days: int,
        own: list[OwnVideo],
        metrics: Mapping[str, VideoMetrics],
    ) -> dict[FormatSegment, ClientBaseline]:
        baselines: dict[FormatSegment, ClientBaseline] = {}
        if report is not None:
            for baseline in sorted(report.baselines, key=lambda item: item.as_of):
                if baseline.metric == "views_at_age" and baseline.age_days == comparison_age_days:
                    baselines[baseline.segment] = ClientBaseline(views_at_age=baseline.median, n=baseline.n)
        if baselines:
            return baselines
        by_segment: dict[FormatSegment, list[tuple[float, int]]] = defaultdict(list)
        for video in own:
            metric = metrics.get(video.video_id)
            if metric is not None and metric.outlier is not None and metric.outlier.baseline_median > 0:
                by_segment[metric.segment].append((metric.outlier.baseline_median, metric.outlier.baseline_n))
        return {
            segment: ClientBaseline(
                views_at_age=statistics.median(value for value, _ in values), n=max(n for _, n in values)
            )
            for segment, values in by_segment.items()
        }
