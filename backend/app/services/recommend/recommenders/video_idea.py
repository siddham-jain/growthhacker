from collections import Counter
from datetime import timedelta

from app.domain.recommendations import EvidenceSource, Reason, RecommendationDraft, RecType, VideoIdeaSubject
from app.services.recommend.context import RecommendContext
from app.services.recommend.evidence.index import EvidenceVideo
from app.services.recommend.evidence.lifts import LiftBook, ci_low
from app.services.recommend.evidence.traits import LOCATION_TRAITS, format_label, trait_kind
from app.services.recommend.reasons import make_reason, select_reasons
from app.services.recommend.recommenders.base import new_draft, rank_by_strength, register_recommender
from app.services.recommend.stats import max_share_within, median
from app.services.recommend.topics import TopicInfo

MIN_LIFT_OUTLIERS = 3


@register_recommender
class VideoIdeaRecommender:
    """topic x winning format x gap: one idea per client topic and its strongest formats."""

    rec_type = RecType.video_idea
    version = 1

    def generate(self, ctx: RecommendContext) -> list[RecommendationDraft]:
        settings = ctx.settings
        lifts = LiftBook(ctx.lifts)
        drafts: list[RecommendationDraft] = []
        for topic in ctx.catalog.ordered():
            if ctx.index.breadth(topic.id) > settings.broad_topic_share:
                continue
            matching = ctx.index.by_topic(topic.id)
            outliers = [video for video in matching if video.is_outlier]
            at_or_above = [video for video in matching if video.ratio >= 1.0]
            if len(outliers) < settings.min_outliers_for_candidate:
                continue
            if len(at_or_above) < settings.gates.min_refs(self.rec_type.value):
                continue
            for format_key in self._formats(ctx, outliers, lifts):
                drafts.append(self._draft(ctx, topic, format_key, at_or_above, outliers, lifts))
        return drafts

    def _formats(
        self, ctx: RecommendContext, outliers: list[EvidenceVideo], lifts: LiftBook
    ) -> list[str | None]:
        excluded = set(ctx.config.excluded_formats)
        counts = Counter(video.format_key for video in outliers if video.format_key not in excluded)
        if not counts:
            return []
        ranked = sorted(
            counts, key=lambda key: (-counts[key], -lifts.ci_low("niche", "format_key", key), key)
        )
        if counts[ranked[0]] < ctx.settings.format_majority_share * len(outliers):
            return [None]
        return [key for key in ranked[:2] if counts[key] >= ctx.settings.min_outliers_for_candidate]

    def _draft(
        self,
        ctx: RecommendContext,
        topic: TopicInfo,
        format_key: str | None,
        candidates: list[EvidenceVideo],
        outliers: list[EvidenceVideo],
        lifts: LiftBook,
    ) -> RecommendationDraft:
        settings = ctx.settings
        in_format = [video for video in outliers if format_key is None or video.format_key == format_key]
        pool = sorted(
            rank_by_strength(candidates),
            key=lambda video: format_key is not None and video.format_key != format_key,
        )[:12]
        subject = VideoIdeaSubject(
            topic_id=topic.id,
            topic_text=topic.label,
            format_key=format_key,
            length_band=format_key.partition("|")[2] if format_key else None,
            title_hint_traits=self._hints(ctx, in_format, lifts, "title"),
            thumbnail_hint_traits=self._hints(ctx, in_format, lifts, "thumbnail"),
            placeholders=self._placeholders(ctx, lifts),
        )
        gap = ctx.gaps.row(topic.id)
        reasons = self._reasons(ctx, topic, format_key, outliers, lifts, gap)
        fit = self._fit(ctx, topic, format_key, gap)
        flags = []
        if max_share_within(
            [video.published_at for video in pool], timedelta(hours=settings.event_span_hours)
        ) >= (settings.event_share):
            flags.append("event_driven")
        if len({video.channel_id for video in outliers}) == 1:
            flags.append("single_channel")
            reasons.append(make_reason("r", "SINGLE_CHANNEL", {"n": len(outliers)}))
        if sum(1 for video in pool if video.review_topic_ids) > 1:
            flags.append("review_flagged_topics")
        question = topic.buyer_question
        return new_draft(
            ctx,
            self,
            f"{topic.id}|{format_key or 'any'}",
            subject=subject,
            topic_ids=[topic.id],
            buyer_question_id=question.id if question else None,
            reference_pool=[video.to_reference() for video in pool],
            reasons=select_reasons(reasons, settings.max_reasons, "r"),
            fit_reasons=select_reasons(fit, settings.max_fit_reasons, "f"),
            gap=gap,
            flags=flags,
        )

    def _reasons(self, ctx, topic, format_key, outliers, lifts, gap) -> list[Reason]:
        ids = [video.video_id for video in outliers]
        reasons = [
            make_reason(
                "r",
                "OUTLIER_SHARE",
                {
                    "k": len(outliers),
                    "n": len(ctx.index.outliers),
                    "window": ctx.window_days,
                    "topic": topic.label,
                },
                ref_video_ids=ids,
            ),
            make_reason(
                "r",
                "MEDIAN_RATIO",
                {
                    "median_ratio": round(median(video.ratio for video in outliers) or 0.0, 1),
                    "n": len(outliers),
                },
                ref_video_ids=ids,
            ),
        ]
        if gap is not None and gap.gap_type == "uncovered" and gap.competitors_covering >= 1:
            reasons.append(
                make_reason(
                    "r",
                    "COMPETITORS_COVER_CLIENT_DOESNT",
                    {"covering": gap.competitors_covering, "total": gap.competitors_total},
                )
            )
        if format_key is not None:
            lift = lifts.best_supported("format_key", format_key, MIN_LIFT_OUTLIERS)
            if lift is not None:
                reasons.append(
                    make_reason(
                        "r",
                        "FORMAT_LIFT",
                        {
                            "trait_label": format_label(format_key),
                            "n_outliers_with": lift.outliers_with,
                            "n_outliers_total": lift.outliers_with + lift.outliers_without,
                            "n_with": lift.n_with,
                            "n_total": lift.n_with + lift.n_without,
                            "lift": round(lift.lift or ci_low(lift) or 1.0, 1),
                        },
                        ref_video_ids=lift.reference_video_ids,
                    )
                )
        return reasons

    def _fit(self, ctx: RecommendContext, topic: TopicInfo, format_key: str | None, gap) -> list[Reason]:
        fit = []
        if topic.is_buyer_question:
            fit.append(make_reason("f", "BUYER_QUESTION_MATCH", {"question": topic.label}))
        else:
            fit.append(make_reason("f", "KEYWORD_MATCH", {"keyword": topic.label}))
        if format_key is not None:
            own = [
                video.ratio
                for video in ctx.index.own_videos
                if video.format_key == format_key and video.ratio
            ]
            if len(own) >= ctx.settings.expected_range.own_min_n:
                fit.append(
                    make_reason(
                        "f",
                        "OWN_FORMAT_RESULT",
                        {
                            "label": format_label(format_key),
                            "ratio": round(median(own) or 0.0, 1),
                            "n": len(own),
                        },
                        source=EvidenceSource.youtube_public,
                    )
                )
        if gap is not None and gap.gap_type == "stale" and gap.client_last_covered_at:
            fit.append(make_reason("f", "CLIENT_STALE_COVERAGE", {"date": gap.client_last_covered_at}))
        if gap is not None and gap.gap_type == "underperforming" and gap.client_best_ratio is not None:
            fit.append(
                make_reason(
                    "f",
                    "CLIENT_UNDERPERFORMING",
                    {"n": gap.client_videos_matching, "ratio": gap.client_best_ratio},
                )
            )
        return fit

    def _hints(
        self, ctx: RecommendContext, outliers: list[EvidenceVideo], lifts: LiftBook, kind: str
    ) -> dict[str, str]:
        if not outliers or not ctx.index.videos:
            return {}
        hints: dict[str, str] = {}
        keys = sorted({key for video in outliers for key in video.traits if trait_kind(key) == kind})
        for key in keys:
            values = Counter(video.traits.get(key) for video in outliers if key in video.traits)
            value, _ = values.most_common(1)[0]
            if value is None or (kind == "title" and value != "true"):
                continue
            share_out = sum(1 for video in outliers if video.traits.get(key) == value) / len(outliers)
            share_all = sum(1 for video in ctx.index.videos if video.traits.get(key) == value) / len(
                ctx.index.videos
            )
            if share_out - share_all >= ctx.settings.hint_share_delta and lifts.best_supported(
                key, value, MIN_LIFT_OUTLIERS
            ):
                hints[key] = value
        return hints

    def _placeholders(self, ctx: RecommendContext, lifts: LiftBook) -> list[str]:
        if not ctx.client.locations:
            return []
        if any(lifts.best_supported(key, "true", MIN_LIFT_OUTLIERS) for key in LOCATION_TRAITS):
            return ["[city]"]
        return []
