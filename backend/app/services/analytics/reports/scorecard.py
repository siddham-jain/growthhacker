from collections import Counter
from datetime import timedelta

from app.domain.analytics import (
    ChannelScorecard,
    CollabMention,
    GapCell,
    LiftScope,
    MedianAtAge,
    OutlierAssessment,
    TraitLift,
)
from app.domain.common import ChannelRole, FormatSegment, subscriber_band
from app.services.analytics.patterns.contingency import MiningUnit
from app.services.analytics.patterns.miner import MiningRequest, PatternMiner
from app.services.analytics.reports.common import coefficient_of_variation, gaps_in_days, median, wilson
from app.services.analytics.reports.context import ScopeView, WindowContext, report_builder_registry
from app.services.analytics.state import VideoState

TOP_CHANNEL_TRAITS = 5


def mining_units(ctx: WindowContext, pairs: list[tuple[VideoState, OutlierAssessment]]) -> list[MiningUnit]:
    traits = ctx.data.traits
    return [
        MiningUnit(
            video_id=s.video_id,
            channel_id=s.channel_id,
            is_outlier=a.is_outlier,
            ratio=a.ratio,
            published_at=s.published_at,
            features=traits[s.video_id].features,
            review_flagged=traits[s.video_id].review_flagged,
            cross_duplicate_group=s.cross_duplicate_group,
        )
        for s, a in pairs
    ]


@report_builder_registry.register("scorecards")
class ScorecardBuilder:
    """evidence sheets of raw per-channel numbers side by side; no composite score and no rank."""

    kind = "scorecards"

    def build(self, ctx: WindowContext) -> list[ChannelScorecard]:
        view = ctx.scopes[LiftScope.competitor_set]
        competitors = [c for c in ctx.data.inputs.channels if c.role is ChannelRole.competitor]
        cells = ctx.gap_cells
        return [
            self._scorecard(ctx, view, scoped.channel.channel_id, cells)
            for scoped in sorted(competitors, key=lambda c: c.channel.channel_id)
        ]

    def _scorecard(
        self, ctx: WindowContext, view: ScopeView, channel_id: str, cells: dict[str, dict[str, GapCell]]
    ) -> ChannelScorecard:
        data, cfg = ctx.data, ctx.settings
        channel = data.channel(channel_id).channel
        available = channel.availability == "available"
        states = [s for s in view.states if s.channel_id == channel_id] if available else []
        pairs = [(s, view.mining[s.video_id]) for s in states if s.video_id in view.mining]
        lookback = data.as_of - timedelta(days=max(cfg.reports.cadence_lookback_days, ctx.window_days))
        history = [
            s for s in data.states.values() if s.channel_id == channel_id and not s.duplicate_secondary
        ]
        recent = sorted(s.published_at for s in history if lookback <= s.published_at <= data.as_of)
        gaps = gaps_in_days(recent)
        last = max((s.published_at for s in history), default=None)
        outliers = sum(a.is_outlier for _, a in pairs)
        engagement = self._engagement(states)
        placements = [
            s.video.has_paid_product_placement
            for s in states
            if s.video.has_paid_product_placement is not None
        ]
        return ChannelScorecard(
            channel_id=channel_id,
            client_id=data.inputs.client.client_id,
            role=ChannelRole.competitor,
            window_days=ctx.window_days,
            as_of=data.as_of,
            uploads_in_window=len(states),
            uploads_per_week=len(states) / (ctx.window_days / 7),
            gap_cv=coefficient_of_variation(gaps)
            if len(recent) >= cfg.reports.min_uploads_for_gap_cv
            else None,
            longest_gap_days=max(gaps, default=None),
            days_since_last_upload=(data.as_of - last).total_seconds() / 86_400 if last else None,
            segment_mix=self._segment_mix(states),
            median_views_at_age=self._median_views(pairs, view),
            median_ratio=median([a.ratio for _, a in pairs]),
            outlier_hit_rate=(outliers, len(pairs)),
            outlier_hit_rate_wilson=wilson(outliers, len(pairs)),
            median_engagement_rate=engagement,
            topic_coverage={
                ref_id: row[channel_id] for ref_id, row in sorted(cells.items()) if channel_id in row
            },
            top_traits=self._top_traits(ctx, view, pairs),
            paid_placement_share=(sum(placements), len(placements)) if placements else None,
            collaborations=self._collaborations(ctx, states),
            subscriber_count=None if channel.hidden_subscriber_count else channel.subscriber_count,
            subscriber_band=subscriber_band(
                None if channel.hidden_subscriber_count else channel.subscriber_count
            ),
            data_sufficiency={
                "channel_available": available,
                "uploads_in_window": bool(states),
                "baseline": bool(pairs),
                "comments": any(
                    (signals := data.comment_signals.get(s.video_id)) is not None and signals.sufficient
                    for s in states
                ),
                "thumbnails": any(data.traits[s.video_id].traits.thumbnail is not None for s in states),
            },
        )

    @staticmethod
    def _segment_mix(states: list[VideoState]) -> dict[FormatSegment, float]:
        counts = Counter(s.segment for s in states)
        return {segment: counts[segment] / len(states) for segment in sorted(counts)} if states else {}

    @staticmethod
    def _median_views(
        pairs: list[tuple[VideoState, OutlierAssessment]], view: ScopeView
    ) -> dict[FormatSegment, MedianAtAge]:
        grouped: dict[FormatSegment, list[OutlierAssessment]] = {}
        for state, assessment in pairs:
            grouped.setdefault(state.segment, []).append(assessment)
        result = {}
        for segment, items in sorted(grouped.items()):
            methods = Counter(a.method for a in items)
            method = min(methods, key=lambda m: (-methods[m], m.value))
            result[segment] = MedianAtAge(
                value=round(median([a.value for a in items])), method=method, n=len(items)
            )
        return result

    @staticmethod
    def _engagement(states: list[VideoState]) -> float | None:
        # engagement is compared only within one basis, so the channel's most common basis is used
        rated = [s for s in states if s.rates.engagement_rate is not None]
        if not rated:
            return None
        bases = Counter(s.rates.basis for s in rated)
        basis = min(bases, key=lambda b: (-bases[b], b))
        return median([s.rates.engagement_rate for s in rated if s.rates.basis == basis])

    @staticmethod
    def _top_traits(
        ctx: WindowContext, view: ScopeView, pairs: list[tuple[VideoState, OutlierAssessment]]
    ) -> list[TraitLift]:
        if not pairs:
            return []
        request = MiningRequest(
            scope=LiftScope.channel,
            window_days=ctx.window_days,
            as_of=ctx.data.as_of,
            comparison_age_days=ctx.age,
            comparison_method=view.method,
            single_channel=True,
        )
        lifts = PatternMiner(ctx.settings.patterns).mine(mining_units(ctx, pairs), request)
        return [lift for lift in lifts if lift.status == "exploratory"][:TOP_CHANNEL_TRAITS]

    @staticmethod
    def _collaborations(ctx: WindowContext, states: list[VideoState]) -> list[CollabMention]:
        mentions = []
        for state in states:
            description = ctx.data.traits[state.video_id].traits.description
            paid = bool(state.video.has_paid_product_placement) or description.has_paid_disclosure_text
            kind = "disclosed_paid" if paid else "mention"
            names = description.mentioned_handles or description.collab_markers
            mentions += [
                CollabMention(video_id=state.video_id, handle_or_name=name, kind=kind) for name in names
            ]
        return sorted(mentions, key=lambda m: (m.video_id, m.handle_or_name))
