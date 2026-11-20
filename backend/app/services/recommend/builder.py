from collections import Counter, defaultdict
from datetime import timedelta

from app.core.config import RecommendSettings
from app.domain.recommendations import RecType
from app.domain.youtube import VideoRecord
from app.services.recommend.context import CompetitorInfo, CreatorPool, RecommendContext
from app.services.recommend.evidence.index import EvidenceIndex, EvidenceIndexBuilder
from app.services.recommend.gaps import GapClassifier
from app.services.recommend.inputs import RecommendInputs, WindowAnalytics
from app.services.recommend.topics import TopicCatalog

DEFAULT_COMPARISON_AGE = {7: 3, 30: 7, 90: 7}
WIDEST_WINDOW = 90


class ContextBuilder:
    """turns the upstream bundle into one immutable context per window; all slicing is anchored on as_of."""

    def __init__(self, settings: RecommendSettings) -> None:
        self.base_settings = settings

    def build(self, inputs: RecommendInputs, window_days: int) -> RecommendContext | None:
        analytics = inputs.window(window_days)
        if analytics is None:
            return None
        settings = self.base_settings.with_overrides(inputs.config.overrides)
        similarity = {(item.topic_a, item.topic_b): item.score for item in inputs.topic_similarity}
        catalog = TopicCatalog(inputs.topic_refs, inputs.buyer_questions, similarity)
        builder = EvidenceIndexBuilder(
            catalog, topic_match_min=settings.topic_match_min, min_ref_age_days=settings.min_ref_age_days
        )
        widest = inputs.window(WIDEST_WINDOW) or analytics
        index = self._index(builder, inputs, analytics, widest, settings)
        index_90 = index if widest is analytics else self._index(builder, inputs, widest, widest, settings)
        competitors = self._competitors(inputs)
        gaps = GapClassifier(
            half_life_days=settings.presence_half_life_days,
            stale_days=settings.stale_coverage_days,
            underperforming_ratio=settings.underperforming_ratio,
        ).report(catalog, index, analytics.gap_matrix, [competitor.channel_id for competitor in competitors])
        stale = inputs.data_refreshed_at is not None and (
            inputs.as_of - inputs.data_refreshed_at > timedelta(hours=settings.stale_data_hours)
        )
        return RecommendContext(
            client_id=inputs.client_id,
            window_days=window_days,
            as_of=inputs.as_of,
            settings=settings,
            lens_mode=inputs.config.lens_mode,
            client=inputs.client,
            catalog=catalog,
            index=index,
            gaps=gaps,
            lifts=tuple(analytics.lifts),
            own_report=analytics.own_report,
            own_channel_id=inputs.own_channel_id,
            competitors=tuple(competitors),
            creators=CreatorPool(
                index_90=index_90,
                channels={channel.channel_id: channel for channel in inputs.channels},
                niche_search_hits=dict(inputs.niche_search_hits),
                candidates=tuple(inputs.competitor_candidates),
                uploads_by_channel=self._uploads(inputs.videos, inputs, settings.creators.lookback_days),
            ),
            config=inputs.config,
            feedback=inputs.feedback,
            previous_cards=dict(inputs.previous_cards),
            stale_data=stale,
            disabled_types=frozenset(RecType(value) for value in inputs.config.disabled_types),
        )

    def _index(
        self,
        builder: EvidenceIndexBuilder,
        inputs: RecommendInputs,
        analytics: WindowAnalytics,
        widest: WindowAnalytics,
        settings: RecommendSettings,
    ) -> EvidenceIndex:
        ages = Counter(metric.comparison_age_days for metric in analytics.metrics.values())
        comparison_age = ages.most_common(1)[0][0] if ages else DEFAULT_COMPARISON_AGE[analytics.window_days]
        traits = {**widest.traits, **analytics.traits}
        return builder.build(
            window_days=analytics.window_days,
            comparison_age_days=comparison_age,
            as_of=inputs.as_of,
            videos=inputs.videos,
            channels={channel.channel_id: channel for channel in inputs.channels},
            roles=inputs.channel_roles,
            own_channel_id=inputs.own_channel_id,
            metrics=analytics.metrics,
            fallback_metrics=widest.metrics,
            traits=traits,
            matches=inputs.topic_matches,
            directness=inputs.directness,
            own_report=analytics.own_report,
            client_lookback_days=settings.client_lookback_days,
        )

    @staticmethod
    def _competitors(inputs: RecommendInputs) -> list[CompetitorInfo]:
        if inputs.competitor_set is None:
            return []
        channels = {channel.channel_id: channel for channel in inputs.channels}
        candidates = {
            match.channel_id: candidate
            for candidate in inputs.competitor_candidates
            for match in candidate.channels
        }
        competitors = []
        for channel_id in inputs.competitor_set.competitor_channel_ids:
            channel = channels.get(channel_id)
            candidate = candidates.get(channel_id)
            name = candidate.brand_name if candidate else (channel.title if channel else channel_id)
            aliases = tuple(sorted({name, *(candidate.aliases if candidate else ())}))
            competitors.append(
                CompetitorInfo(
                    channel_id=channel_id,
                    name=name,
                    handle=channel.handle if channel else None,
                    aliases=aliases,
                    domain=candidate.official_domain if candidate else None,
                )
            )
        return competitors

    @staticmethod
    def _uploads(
        videos: list[VideoRecord], inputs: RecommendInputs, lookback_days: int
    ) -> dict[str, tuple[VideoRecord, ...]]:
        start = inputs.as_of - timedelta(days=lookback_days)
        grouped: dict[str, list[VideoRecord]] = defaultdict(list)
        for video in videos:
            if start <= video.published_at <= inputs.as_of:
                grouped[video.channel_id].append(video)
        return {
            channel_id: tuple(
                sorted(items, key=lambda item: (item.published_at, item.video_id), reverse=True)
            )
            for channel_id, items in grouped.items()
        }
