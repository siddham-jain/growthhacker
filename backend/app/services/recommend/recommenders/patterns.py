from typing import ClassVar

from app.domain.analytics import TraitLift
from app.domain.recommendations import PatternSpec, Reason, RecommendationDraft, RecType
from app.services.recommend.context import RecommendContext
from app.services.recommend.evidence.index import EvidenceVideo
from app.services.recommend.evidence.lifts import LiftBook, ci_high, ci_low, lift_traits, sample_met
from app.services.recommend.evidence.traits import PatternKind, format_label, traits_label
from app.services.recommend.reasons import make_reason, select_reasons
from app.services.recommend.recommenders.base import (
    new_draft,
    own_with_traits,
    rank_by_strength,
    recent_own,
    register_recommender,
)
from app.services.recommend.stats import median

MIN_PATTERN_OUTLIERS = 3
SCOPE_LABELS = {"competitor_set": "your competitors", "niche": "the wider niche"}


class PatternRecommender:
    """shared generation for title, thumbnail and format patterns from 06's trait lifts."""

    rec_type: ClassVar[RecType]
    kind: ClassVar[PatternKind]
    version = 1

    def generate(self, ctx: RecommendContext) -> list[RecommendationDraft]:
        book = LiftBook(ctx.lifts)
        drafts = []
        for lift in book.pattern_candidates(self.kind, MIN_PATTERN_OUTLIERS):
            draft = self._draft(ctx, book, lift)
            if draft is not None:
                drafts.append(draft)
        return drafts

    def _draft(self, ctx: RecommendContext, book: LiftBook, lift: TraitLift) -> RecommendationDraft | None:
        settings = ctx.settings
        traits = lift_traits(lift)
        if self._excluded(ctx, traits):
            return None
        pool = [video for video in ctx.index.with_traits(traits) if video.is_outlier and video.topic_ids]
        if len(pool) < settings.gates.min_refs(self.rec_type.value):
            return None
        if len({video.channel_id for video in pool}) < settings.gates.min_channels(self.rec_type.value):
            return None
        own_recent = recent_own(ctx)
        own_with = own_with_traits(own_recent, traits)
        own_ratios = [video.ratio for video in own_with_traits(ctx.index.own_videos, traits) if video.ratio]
        subject = PatternSpec(
            kind=self.kind,
            traits=traits,
            scope=lift.scope,
            lift=round(lift.lift or 1.0, 2),
            ci_low=round(ci_low(lift) or 0.0, 2),
            ci_high=round(ci_high(lift) or 0.0, 2),
            n_with=lift.n_with,
            n_outliers_with=lift.outliers_with,
            n_total=lift.n_with + lift.n_without,
            n_outliers_total=lift.outliers_with + lift.outliers_without,
            client_usage_share=round(len(own_with) / len(own_recent), 3) if own_recent else None,
            client_ratio_with=round(median(own_ratios), 2) if own_ratios else None,
            client_n_with=len(own_ratios),
        )
        ranked = rank_by_strength(pool)[:12]
        label = self.label(traits)
        reasons = [
            make_reason(
                "r",
                "TRAIT_LIFT",
                {
                    "trait_label": label,
                    "n_outliers_with": subject.n_outliers_with,
                    "n_outliers_total": subject.n_outliers_total,
                    "n_with": subject.n_with,
                    "n_total": subject.n_total,
                    "lift": round(subject.lift, 1),
                },
                ref_video_ids=lift.reference_video_ids,
            ),
            make_reason(
                "r",
                "MEDIAN_RATIO",
                {"median_ratio": round(median(video.ratio for video in pool) or 0.0, 1), "n": len(pool)},
                ref_video_ids=[video.video_id for video in pool],
            ),
        ]
        flags = []
        opposite = book.opposite(lift)
        if opposite is not None and sample_met(opposite) and (ci_high(opposite) or 2.0) < 1.0:
            flags.append("contradictory")
            if min(lift.outliers_with, opposite.outliers_with) >= settings.contradiction_large_n:
                flags.append("contradictory_large_samples")
            reasons.append(
                make_reason(
                    "r",
                    "MIXED_EVIDENCE",
                    {
                        "scope_a": SCOPE_LABELS[lift.scope.value],
                        "scope_b": SCOPE_LABELS[opposite.scope.value],
                    },
                )
            )
        fit = self._fit(ctx, subject, label, own_recent, own_with, pool)
        return new_draft(
            ctx,
            self,
            # the scope can flip between runs as samples grow, so it stays out of the card's identity
            f"{self.kind}|{'|'.join(f'{key}={value}' for key, value in sorted(traits.items()))}",
            subject=subject,
            topic_ids=sorted({topic for video in pool for topic in video.topic_ids}),
            reference_pool=[video.to_reference() for video in ranked],
            reasons=select_reasons(reasons, settings.max_reasons, "r"),
            fit_reasons=select_reasons(fit, settings.max_fit_reasons, "f"),
            flags=flags,
        )

    def label(self, traits: dict[str, str]) -> str:
        return traits_label(traits)

    def _excluded(self, ctx: RecommendContext, traits: dict[str, str]) -> bool:
        return False

    def _fit(
        self,
        ctx: RecommendContext,
        subject: PatternSpec,
        label: str,
        own_recent: list,
        own_with: list,
        pool: list[EvidenceVideo],
    ) -> list[Reason]:
        fit = []
        if own_recent:
            fit.append(
                make_reason(
                    "f", "CLIENT_TRAIT_USAGE", {"k": len(own_with), "n": len(own_recent), "label": label}
                )
            )
        if (
            subject.client_n_with >= ctx.settings.expected_range.own_min_n
            and subject.client_ratio_with is not None
        ):
            fit.append(
                make_reason(
                    "f",
                    "OWN_TRAIT_RESULT",
                    {
                        "label": label,
                        "ratio": round(subject.client_ratio_with, 1),
                        "n": subject.client_n_with,
                    },
                )
            )
        question_ids = {topic.id for topic in ctx.catalog.buyer_questions()}
        answering = [video for video in pool if video.topic_ids & question_ids]
        if answering:
            fit.append(
                make_reason(
                    "f",
                    "TOPIC_SUPPORT",
                    {"k": len(answering), "n": len(pool)},
                    ref_video_ids=[v.video_id for v in answering],
                )
            )
        else:
            fit.append(make_reason("f", "TOPIC_SUPPORT", {"k": len(pool), "n": len(pool)}))
        return fit


@register_recommender
class TitlePatternRecommender(PatternRecommender):
    rec_type = RecType.title_pattern
    kind = "title"


@register_recommender
class ThumbnailPatternRecommender(PatternRecommender):
    rec_type = RecType.thumbnail_pattern
    kind = "thumbnail"


@register_recommender
class FormatRecommender(PatternRecommender):
    rec_type = RecType.format
    kind = "format"

    def label(self, traits: dict[str, str]) -> str:
        if set(traits) == {"format_key"}:
            return format_label(traits["format_key"])
        return traits_label(traits)

    def _excluded(self, ctx: RecommendContext, traits: dict[str, str]) -> bool:
        excluded = set(ctx.config.excluded_formats)
        return traits.get("format_key") in excluded

    def _fit(self, ctx, subject, label, own_recent, own_with, pool) -> list[Reason]:
        fit = []
        if (
            subject.client_n_with >= ctx.settings.expected_range.own_min_n
            and subject.client_ratio_with is not None
        ):
            fit.append(
                make_reason(
                    "f",
                    "OWN_FORMAT_RESULT",
                    {
                        "label": label,
                        "ratio": round(subject.client_ratio_with, 1),
                        "n": subject.client_n_with,
                    },
                )
            )
        elif own_recent and not own_with:
            fit.append(make_reason("f", "CLIENT_FORMAT_ABSENT", {"n": len(own_recent), "label": label}))
        weak_topics = self._underperforming_topics(ctx, subject.traits, pool)
        if weak_topics:
            fit.append(
                make_reason(
                    "f",
                    "UNDERPERFORMING_TOPICS",
                    {"k": weak_topics, "ratio": ctx.settings.underperforming_ratio},
                )
            )
        return fit or super()._fit(ctx, subject, label, own_recent, own_with, pool)

    def _underperforming_topics(
        self, ctx: RecommendContext, traits: dict[str, str], pool: list[EvidenceVideo]
    ) -> int:
        # client topics where the client used another format and ran below normal while references use this one
        topics = {topic for video in pool for topic in video.topic_ids}
        weak = 0
        for topic_id in sorted(topics):
            others = [
                video.ratio
                for video in ctx.index.own_by_topic(topic_id)
                if video.ratio is not None
                and any(video.traits.get(key) != value for key, value in traits.items())
            ]
            if others and (median(others) or 0.0) < ctx.settings.underperforming_ratio:
                weak += 1
        return weak
