from collections import Counter
from datetime import timedelta

from app.domain.analytics import AgeMethod, LiftScope, TopicTrend, TrendingItem, TrendingReport
from app.services.analytics.reports.common import median
from app.services.analytics.reports.context import SCOPE_ROLES, WindowContext, report_builder_registry
from app.services.analytics.state import VideoState


def _sort_key(item: TrendingItem, published: dict[str, object]) -> tuple:
    return (
        item.ratio is None,
        -(item.ratio or 0.0),
        item.velocity_ratio is None,
        -(item.velocity_ratio or 0.0),
        -published[item.video_id].timestamp(),
        item.video_id,
    )


@report_builder_registry.register("trending")
class TrendingBuilder:
    """trending in the niche = cached niche search plus same-age ratios and velocity; never the mostPopular chart."""

    kind = "trending"

    def build(self, ctx: WindowContext) -> TrendingReport:
        cfg = ctx.settings
        view = ctx.scopes[LiftScope.niche]
        items = [item for state in view.states if (item := self._item(ctx, state)) is not None]
        published = {s.video_id: s.published_at for s in view.states}
        per_channel: Counter[str] = Counter()
        selected = []
        for item in sorted(items, key=lambda i: _sort_key(i, published)):
            if per_channel[item.channel_id] >= cfg.lists.max_per_channel_in_lists:
                continue
            per_channel[item.channel_id] += 1
            selected.append(item)
            if len(selected) >= cfg.lists.trending_top_n:
                break
        messages = [] if selected else ["No trending videos for your topics in this window."]
        return TrendingReport(
            client_id=ctx.data.inputs.client.client_id,
            window_days=ctx.window_days,
            as_of=ctx.data.as_of,
            items=selected,
            rising_topics=self._rising(ctx),
            sufficiency_messages=messages,
        )

    def _item(self, ctx: WindowContext, state: VideoState) -> TrendingItem | None:
        cfg = ctx.settings
        topics = ctx.data.traits[state.video_id].traits.topics
        if not topics:
            return None
        assessment = ctx.display(state)
        velocity = self._velocity_ratio(ctx, state)
        if assessment is not None:
            if assessment.suppressed_reason is not None or assessment.value < cfg.lists.trending_min_views:
                return None
            ratio, method = assessment.ratio, assessment.method
        elif velocity is not None and state.views_now >= cfg.lists.trending_min_views:
            # too new to judge at the comparison age; shown with velocity only
            ratio, method = None, AgeMethod.pending
        else:
            return None
        return TrendingItem(
            video_id=state.video_id,
            channel_id=state.channel_id,
            ratio=ratio,
            method=method,
            velocity_ratio=velocity,
            views_per_day=state.views_now / max(state.age_now, 1.0),
            topic_ref_ids=[t.topic_ref_id for t in topics],
            segment=state.segment,
        )

    @staticmethod
    def _velocity_ratio(ctx: WindowContext, state: VideoState) -> float | None:
        # recent velocity against same-channel, same-segment peers at a similar age; lifetime views/day at t0
        cfg = ctx.settings
        kappa = cfg.proxy.kappa(state.age_now)
        peers = [
            s
            for s in ctx.data.states.values()
            if s.channel_id == state.channel_id
            and s.segment is state.segment
            and s.video_id != state.video_id
            and not s.duplicate_secondary
            and state.age_now / kappa <= s.age_now <= state.age_now * kappa
        ]

        def usable(s: VideoState) -> bool:
            v = s.velocity_recent
            return v is not None and not s.series.is_frozen_between(v.from_at, v.to_at)

        if usable(state):
            values = [s.velocity_recent.views_per_day for s in peers if usable(s)]
            own = state.velocity_recent.views_per_day
        else:
            values = [s.views_now / max(s.age_now, 1.0) for s in peers]
            own = state.views_now / max(state.age_now, 1.0)
        baseline = median(values) if len(values) >= cfg.rates.velocity_ratio_min_peers else None
        if baseline is None or baseline <= 0:
            return None
        return own / baseline

    @staticmethod
    def _rising(ctx: WindowContext) -> list[TopicTrend]:
        cfg, data = ctx.settings, ctx.data
        channels = data.available_channel_ids(SCOPE_ROLES[LiftScope.niche])
        previous_start = ctx.start - timedelta(days=ctx.window_days)
        windows = {
            "now": list(ctx.scopes[LiftScope.niche].states),
            "prev": [
                s
                for s in data.published_between(previous_start, ctx.start, channels)
                if s.published_at < ctx.start and not s.duplicate_secondary
            ],
        }
        trends = []
        for ref in sorted(data.inputs.topic_refs, key=lambda r: r.id):
            counts = {}
            for name, states in windows.items():
                matching = [s for s in states if ref.id in _topic_ids(ctx, s)]
                hits = sum(1 for s in matching if (a := ctx.display(s)) is not None and a.is_outlier)
                counts[name] = (len(matching), hits)
            (uploads_now, outliers_now), (uploads_prev, outliers_prev) = counts["now"], counts["prev"]
            if (
                outliers_now >= cfg.lists.rising_min_outliers
                and outliers_now >= cfg.lists.rising_factor * outliers_prev
            ):
                trends.append(
                    TopicTrend(
                        topic_ref_id=ref.id,
                        uploads_now=uploads_now,
                        uploads_prev=uploads_prev,
                        outliers_now=outliers_now,
                        outliers_prev=outliers_prev,
                    )
                )
        return trends


def _topic_ids(ctx: WindowContext, state: VideoState) -> set[str]:
    return {t.topic_ref_id for t in ctx.data.traits[state.video_id].traits.topics}
