from app.domain.recommendations import (
    CreatorSubject,
    EvidenceSource,
    Lens,
    LensAssignment,
    PatternSpec,
    Reason,
    RecommendationDraft,
    RecType,
    VideoIdeaSubject,
)
from app.services.recommend.context import RecommendContext
from app.services.recommend.evidence.index import BREAKOUT_TIERS
from app.services.recommend.reasons import make_reason, select_reasons
from app.services.recommend.stats import median

LENS_ORDER = (Lens.views, Lens.conversion)
MIN_VIEWS_OUTLIERS = 3
BREAKOUT_SHARE = 0.5
CREATOR_MIN_DIRECT = 2


class LensAssigner:
    """deterministic lens rules; each lens carries the reasons that put it there. website data never assigns one."""

    def assign(self, draft: RecommendationDraft, ctx: RecommendContext) -> list[LensAssignment]:
        conversion = self._conversion(draft, ctx)
        views = self._views(draft, ctx)
        if (
            draft.expected_range is not None
            and draft.expected_range.high is not None
            and draft.expected_range.high < 1.0
        ):
            views = []
        lenses = {Lens.views: views, Lens.conversion: conversion}
        return [
            LensAssignment(
                lens=lens, reasons=select_reasons(lenses[lens], len(lenses[lens]), f"{lens.value[0]}l")
            )
            for lens in LENS_ORDER
            if lenses[lens]
        ]

    def _conversion(self, draft: RecommendationDraft, ctx: RecommendContext) -> list[Reason]:
        reasons: list[Reason] = []
        if draft.type is RecType.creator:
            return self._creator_conversion(draft, ctx)
        topic = ctx.catalog.for_question(draft.buyer_question_id)
        if topic is None:
            return reasons
        refs = draft.references
        direct = [
            ref.video_id
            for ref in refs
            if (video := ctx.index.get(ref.video_id))
            and video.directly_answers(topic.id, ctx.settings.direct_match_min)
        ]
        if refs and len(direct) / len(refs) >= ctx.settings.directness_share_min:
            stage = topic.stage.value if topic.stage else "unknown"
            reasons.append(
                make_reason(
                    "l",
                    "ANSWERS_BUYER_QUESTION",
                    {"k": len(direct), "n": len(refs), "stage": stage},
                    ref_video_ids=direct,
                )
            )
        cited = topic.buyer_question.ai_cited_video_ids if topic.buyer_question else None
        if cited:
            competitor_ids = set(ctx.competitor_ids)
            competitor_videos = {
                record.video_id
                for channel_id, records in ctx.creators.uploads_by_channel.items()
                if channel_id in competitor_ids
                for record in records
            }
            own_videos = {video.video_id for video in ctx.index.own_videos}
            competitor_hits = sum(1 for video_id in cited if video_id in competitor_videos)
            own_hits = sum(1 for video_id in cited if video_id in own_videos)
            if competitor_hits and not own_hits:
                reasons.append(
                    make_reason(
                        "l",
                        "AI_CITES_COMPETITOR",
                        {"competitor_citations": competitor_hits, "client_citations": own_hits},
                        source=EvidenceSource.spotlit_ai_tracking,
                    )
                )
        own_subs = self._own_subscribers(draft, ctx)
        if own_subs is not None:
            reasons.append(own_subs)
        return reasons

    def _own_subscribers(self, draft: RecommendationDraft, ctx: RecommendContext) -> Reason | None:
        report = ctx.own_report
        if report is None or report.conversion.own_channel is None:
            return None
        per_1k = report.conversion.own_channel.subscribers_gained_per_1k_views
        subject = draft.subject
        if not isinstance(subject, VideoIdeaSubject) or not per_1k:
            return None
        similar = [
            per_1k[video.video_id]
            for video in ctx.index.own_by_topic(subject.topic_id)
            if video.video_id in per_1k and subject.format_key in (None, video.format_key)
        ]
        overall = median(per_1k.values())
        value = median(similar)
        if value is None or overall is None or value < overall:
            return None
        return make_reason(
            "l",
            "OWN_SUBS_PER_1K",
            {"value": round(value, 1), "median": round(overall, 1)},
            source=EvidenceSource.youtube_own_analytics,
        )

    def _creator_conversion(self, draft: RecommendationDraft, ctx: RecommendContext) -> list[Reason]:
        questions = [topic.id for topic in ctx.catalog.buyer_questions()]
        direct = [
            ref.video_id
            for ref in draft.reference_pool
            if (video := ctx.index.get(ref.video_id))
            and any(video.directly_answers(question, ctx.settings.direct_match_min) for question in questions)
        ]
        if len(direct) < CREATOR_MIN_DIRECT:
            return []
        return [make_reason("l", "CREATOR_ANSWERS_BQ", {"k": len(direct)}, ref_video_ids=direct)]

    def _views(self, draft: RecommendationDraft, ctx: RecommendContext) -> list[Reason]:
        refs = draft.references
        if not refs:
            return []
        if draft.type is RecType.creator:
            subject = draft.subject
            assert isinstance(subject, CreatorSubject)
            ratio = median(ref.outlier_ratio or 0.0 for ref in draft.reference_pool)
            if ratio is None or ratio < 1.0 or not any(ref.is_outlier for ref in draft.reference_pool):
                return []
            return [
                make_reason(
                    "l",
                    "CREATOR_PERFORMANCE",
                    {"median_ratio": round(ratio, 1), "n": len(draft.reference_pool)},
                )
            ]
        if isinstance(draft.subject, PatternSpec) and draft.subject.ci_low > 1.0:
            return [reason for reason in draft.reasons if reason.code == "TRAIT_LIFT"][:1]
        outliers = [ref for ref in refs if ref.is_outlier]
        breakout = [
            ref
            for ref in refs
            if (video := ctx.index.get(ref.video_id)) is not None and video.tier in BREAKOUT_TIERS
        ]
        if len(outliers) >= MIN_VIEWS_OUTLIERS and len(breakout) / len(refs) >= BREAKOUT_SHARE:
            ratio = median(ref.outlier_ratio or 0.0 for ref in refs) or 0.0
            return [make_reason("l", "MEDIAN_RATIO", {"median_ratio": round(ratio, 1), "n": len(refs)})]
        if draft.type is RecType.description_refresh and draft.reasons:
            # refresh targets search views on an existing video; 09 supplies the evidence reason
            return draft.reasons[:1]
        return []
