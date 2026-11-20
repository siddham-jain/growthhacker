from app.domain.common import subscriber_band
from app.domain.recommendations import (
    CreatorConsistency,
    CreatorPerformance,
    CreatorRelevance,
    CreatorSubject,
    Reason,
    RecommendationDraft,
    RecType,
)
from app.services.recommend.context import RecommendContext
from app.services.recommend.creators.collabs import CollaborationDetector, collaboration_label
from app.services.recommend.creators.ranker import (
    CreatorRanker,
    CreatorStats,
    gap_stats,
    hit_rate,
    order_creators,
)
from app.services.recommend.reasons import make_reason, select_reasons
from app.services.recommend.recommenders.base import new_draft, rank_by_strength, register_recommender
from app.services.recommend.stats import median

MAX_COLLAB_REASONS = 1
MAX_TOPIC_LABELS = 3
TOPICS_TEXT_MAX_CHARS = 100


@register_recommender
class CreatorRecommender:
    """creators judged only by what they publish and how it performs on their own channel."""

    rec_type = RecType.creator
    version = 1

    def generate(self, ctx: RecommendContext) -> list[RecommendationDraft]:
        settings = ctx.settings.creators
        keywords = [
            *ctx.client.niche_keywords,
            *(topic.label for topic in ctx.catalog.all() if not topic.is_buyer_question),
        ]
        ranker = CreatorRanker(CollaborationDetector(settings.sponsor_phrases, keywords))
        drafts = [
            self._draft(ctx, stats) for stats in ranker.candidates(ctx) if CreatorRanker.eligible(ctx, stats)
        ]
        criterion = ctx.config.creator_sort or settings.default_sort
        return order_creators(drafts, criterion, settings.strong_match_share)[: settings.to_gate]

    def _draft(self, ctx: RecommendContext, stats: CreatorStats) -> RecommendationDraft:
        settings = ctx.settings
        channel = stats.channel
        median_gap, gap_cv = gap_stats(stats, settings.creators.min_uploads_for_gaps)
        niche_views = median(
            video.views_at_age.views or 0
            for video in ctx.creators.index_90.videos
            if video.role.value != "own"
        )
        handle = (
            channel.handle
            if channel.handle is None or channel.handle.startswith("@")
            else f"@{channel.handle}"
        )
        url = (
            f"https://www.youtube.com/{handle}"
            if handle
            else f"https://www.youtube.com/channel/{channel.channel_id}"
        )
        subject = CreatorSubject(
            channel_id=channel.channel_id,
            title=channel.title,
            handle=handle,
            url=url,
            subscriber_band=subscriber_band(
                None if channel.hidden_subscriber_count else channel.subscriber_count
            ),
            declared_country=channel.country,
            uploads_90d=len(stats.upload_times),
            uploads_checked=len(stats.uploads),
            relevance=CreatorRelevance(
                match_share=round(stats.match_share, 3), topics_covered=len(stats.topics_covered)
            ),
            performance=CreatorPerformance(
                median_outlier_ratio=round(stats.median_ratio, 2) if stats.median_ratio is not None else None,
                median_views_at_age=stats.median_views,
                niche_median_views_at_age=round(niche_views) if niche_views is not None else None,
                comparison_age_days=ctx.creators.index_90.comparison_age_days,
            ),
            consistency=CreatorConsistency(
                median_gap_days=median_gap,
                gap_cv=gap_cv,
                hit_rate=hit_rate(stats),
                last_upload_at=stats.last_upload_at.date() if stats.last_upload_at else None,
            ),
            collaborations=list(stats.collaborations),
        )
        refs = rank_by_strength(stats.matching_in_window)[:12]
        return new_draft(
            ctx,
            self,
            channel.channel_id,
            subject=subject,
            topic_ids=sorted({topic for video in refs for topic in video.topic_ids}),
            reference_pool=[video.to_reference() for video in refs],
            reasons=select_reasons(self._reasons(ctx, stats, median_gap), settings.max_reasons, "r"),
            fit_reasons=select_reasons(self._fit(ctx, stats), settings.max_fit_reasons, "f"),
            allowed_dates=sorted({item.published_at.date() for item in stats.collaborations}),
            entities=sorted({channel.title, *(item.competitor_name for item in stats.collaborations)}),
        )

    def _reasons(self, ctx: RecommendContext, stats: CreatorStats, median_gap: float | None) -> list[Reason]:
        reasons = [
            make_reason(
                "r",
                "CREATOR_RELEVANCE",
                {"k": len(stats.matching), "n": len(stats.uploads), "topics": len(stats.topics_covered)},
                ref_video_ids=[video.video_id for video in stats.matching],
            )
        ]
        if stats.median_ratio is not None:
            reasons.append(
                make_reason(
                    "r",
                    "CREATOR_PERFORMANCE",
                    {"median_ratio": round(stats.median_ratio, 1), "n": len(stats.matching)},
                    ref_video_ids=[video.video_id for video in stats.matching],
                )
            )
        if median_gap is not None:
            reasons.append(
                make_reason(
                    "r",
                    "CREATOR_CONSISTENCY",
                    {
                        "uploads": len(stats.upload_times),
                        "days": ctx.settings.creators.lookback_days,
                        "gap_days": median_gap,
                    },
                )
            )
        for item in stats.collaborations[:MAX_COLLAB_REASONS]:
            reasons.append(
                make_reason(
                    "r", "CREATOR_COLLAB", {"label": collaboration_label(item)}, ref_video_ids=[item.video_id]
                )
            )
        return reasons

    def _fit(self, ctx: RecommendContext, stats: CreatorStats) -> list[Reason]:
        labels: list[str] = []
        for topic in ctx.catalog.ordered():
            if (
                topic.id in stats.topics_covered
                and len("; ".join([*labels, topic.label])) <= TOPICS_TEXT_MAX_CHARS
            ):
                labels.append(topic.label)
        return [
            make_reason("f", "CREATOR_TOPIC_MATCH", {"topics_text": "; ".join(labels[:MAX_TOPIC_LABELS])}),
            make_reason(
                "f",
                "CREATOR_COLLAB_COVERAGE",
                {
                    "creator_uploads": len(stats.uploads),
                    "competitor_uploads": stats.competitor_uploads_checked,
                },
            ),
        ]
