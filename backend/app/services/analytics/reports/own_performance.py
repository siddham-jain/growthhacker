from collections import Counter
from datetime import timedelta

from app.domain.analytics import (
    AgeMethod,
    ConsistencyStats,
    OutlierAssessment,
    OutlierTier,
    OwnPerformanceReport,
    SegmentPerf,
    TraitPerf,
    VideoPerf,
)
from app.domain.common import ChannelRole
from app.services.analytics.reports.common import (
    coefficient_of_variation,
    gaps_in_days,
    iqr,
    log_iqr,
    median,
    ratio_key,
)
from app.services.analytics.reports.context import WindowContext, report_builder_registry
from app.services.analytics.reports.conversion import conversion_lens
from app.services.analytics.state import VideoState

NO_OWN_BASELINE = "We can't compare you with yourself yet. Results are shown relative to the niche."


def _group(rows: list[tuple[str, float]], min_n: int) -> dict[str, SegmentPerf]:
    grouped: dict[str, list[float]] = {}
    for key, ratio in rows:
        grouped.setdefault(key, []).append(ratio)
    return {
        key: SegmentPerf(
            n=len(values),
            median_ratio=median(values) if len(values) >= min_n else None,
            iqr=iqr(values) if len(values) >= min_n else None,
        )
        for key, values in sorted(grouped.items())
    }


@report_builder_registry.register("own_performance")
class OwnPerformanceBuilder:
    kind = "own_performance"

    def build(self, ctx: WindowContext) -> OwnPerformanceReport | None:
        data, cfg = ctx.data, ctx.settings
        own_id = data.inputs.own_channel_id
        if own_id is None or own_id not in data.available_channel_ids((ChannelRole.own,)):
            return None
        states = ctx.window_states({own_id})
        assessed = [(s, a) for s in states if (a := ctx.display(s)) is not None]
        judged = [(s, a) for s, a in assessed if a.tier is not OutlierTier.insufficient]
        previous_start = ctx.start - timedelta(days=ctx.window_days)
        previous = [
            a.ratio
            for s in data.published_between(previous_start, ctx.start, {own_id})
            if not s.duplicate_secondary and s.published_at < ctx.start
            if (a := ctx.display(s)) is not None and a.tier is not OutlierTier.insufficient
        ]
        # suppressed spikes are listed elsewhere and never presented as the channel's best work
        ordered = sorted(
            ((s, a) for s, a in judged if a.suppressed_reason is None), key=lambda x: ratio_key(*x)
        )
        count = cfg.lists.best_worst_n
        messages = self._messages(states, judged, cfg.lists.min_group_n) + self._skew_message(ctx, assessed)
        return OwnPerformanceReport(
            client_id=data.inputs.client.client_id,
            channel_id=own_id,
            window_days=ctx.window_days,
            as_of=data.as_of,
            comparison_age_days=ctx.age,
            method_mix=dict(sorted(Counter(a.method for _, a in assessed).items())),
            videos=[self._perf(ctx, s, a) for s, a in sorted(assessed, key=lambda x: x[0].ordering_key())],
            median_ratio_window=median([a.ratio for _, a in judged]),
            median_ratio_prev_window=median(previous),
            best=[s.video_id for s, _ in ordered[:count]],
            worst=[s.video_id for s, _ in reversed(ordered[-count:])] if ordered else [],
            by_format=_group(
                [(s.segment.value, a.ratio) for s, a in judged]
                + [(data.traits[s.video_id].traits.format_key, a.ratio) for s, a in judged],
                cfg.lists.min_group_n,
            ),
            by_topic=_group(
                [(t.topic_ref_id, a.ratio) for s, a in judged for t in data.traits[s.video_id].traits.topics],
                cfg.lists.min_group_n,
            ),
            by_trait=self._by_trait(ctx, judged),
            consistency=self._consistency(ctx, own_id, judged),
            baselines=sorted(
                {a.baseline_id: data.book.baseline_of(a).baseline for _, a in assessed}.values(),
                key=lambda b: b.baseline_id,
            ),
            conversion=conversion_lens(ctx, {own_id}, per_channel_keys=False),
            sufficiency_messages=messages,
        )

    @staticmethod
    def _perf(ctx: WindowContext, state: VideoState, assessment: OutlierAssessment) -> VideoPerf:
        traits = ctx.data.traits[state.video_id].traits
        return VideoPerf(
            video_id=state.video_id,
            published_at=state.published_at,
            segment=state.segment,
            format_key=traits.format_key,
            ratio=None if assessment.tier is OutlierTier.insufficient else assessment.ratio,
            tier=assessment.tier,
            method=assessment.method,
            topic_ref_ids=[t.topic_ref_id for t in traits.topics],
        )

    @staticmethod
    def _by_trait(
        ctx: WindowContext, judged: list[tuple[VideoState, OutlierAssessment]]
    ) -> dict[str, TraitPerf]:
        rows: dict[str, list[float]] = {}
        for state, assessment in judged:
            for key, values in ctx.data.traits[state.video_id].features.items():
                for value in values:
                    rows.setdefault(f"{key}={value}", []).append(assessment.ratio)
        total = len(judged)
        min_n = ctx.settings.lists.min_group_n
        return {
            key: TraitPerf(
                median_ratio=median(ratios) if len(ratios) >= min_n else None,
                n=len(ratios),
                usage_share=len(ratios) / total,
            )
            for key, ratios in sorted(rows.items())
        }

    @staticmethod
    def _consistency(
        ctx: WindowContext, own_id: str, judged: list[tuple[VideoState, OutlierAssessment]]
    ) -> ConsistencyStats:
        data, cfg = ctx.data, ctx.settings
        uploads = ctx.window_states({own_id})
        lookback = data.as_of - timedelta(days=max(cfg.reports.cadence_lookback_days, ctx.window_days))
        recent = [
            s.published_at
            for s in data.published_between(lookback, data.as_of, {own_id})
            if not s.duplicate_secondary
        ]
        gap_cv = (
            coefficient_of_variation(gaps_in_days(recent))
            if len(recent) >= cfg.reports.min_uploads_for_gap_cv
            else None
        )
        ratios = [a.ratio for _, a in judged]
        return ConsistencyStats(
            uploads_per_week=len(uploads) / (ctx.window_days / 7),
            gap_cv=gap_cv,
            hit_rate_at_or_above_baseline=sum(r >= 1 for r in ratios) / len(ratios) if ratios else None,
            log_ratio_iqr=log_iqr(ratios),
        )

    @staticmethod
    def _skew_message(ctx: WindowContext, assessed: list[tuple[VideoState, OutlierAssessment]]) -> list[str]:
        # the mean is shown for reference only; say how many videos skew it
        for _, assessment in assessed:
            baseline = ctx.data.book.baseline_of(assessment).baseline
            members = [ctx.data.states[v] for v in baseline.video_ids if v in ctx.data.states]
            skewed = sum(1 for m in members if m.views_now > 10 * max(baseline.median, 1.0))
            if skewed and baseline.mean > 2 * baseline.median:
                return [f"Average skewed by {skewed} video(s) above 10x normal. We use the median."]
        return []

    @staticmethod
    def _messages(
        states: list[VideoState], judged: list[tuple[VideoState, OutlierAssessment]], min_n: int
    ) -> list[str]:
        messages = []
        if not judged:
            messages.append(NO_OWN_BASELINE)
        segments = Counter(s.segment.value for s, _ in judged)
        for segment, n in sorted(segments.items()):
            if n < min_n:
                messages.append(
                    f"Only {n} {segment.replace('_', '-')} video(s) in this window, not enough to summarise."
                )
        if any(a.method is AgeMethod.age_band_proxy for _, a in judged):
            messages.append(
                "Older videos are compared at their current age (true same-age data expires after 30 days)."
            )
        if not states:
            messages.append("No uploads in this window.")
        return messages
