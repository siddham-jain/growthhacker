from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timedelta

from app.domain.analytics import CommentSignals, LiftScope, MetricFlag, TraitLift
from app.domain.common import ChannelRole
from app.domain.topics import AnswerDirectness, TopicMatch
from app.services.analytics.comments.lexicon import build_lexicon
from app.services.analytics.comments.signals import CommentSignalExtractor
from app.services.analytics.inputs import AnalyticsInputs
from app.services.analytics.patterns.miner import MiningRequest, PatternMiner
from app.services.analytics.reports import (
    conversion,
    gap_matrix,
    outliers,
    own_performance,
    scorecard,
    trending,
)
from app.services.analytics.reports.context import (
    SCOPE_ROLES,
    EngineData,
    WindowContext,
    build_scope,
    report_builder_registry,
)
from app.services.analytics.reports.scorecard import mining_units
from app.services.analytics.result import AnalyticsResult, WindowAnalytics
from app.services.analytics.settings import AnalyticsEngineSettings
from app.services.analytics.state import (
    METRICS_VERSION,
    AssessmentBook,
    StateBuilder,
    VideoState,
    build_metrics,
)
from app.services.analytics.thumbnails.analyzer import TRAITS_VERSION as THUMBNAIL_TRAITS_VERSION
from app.services.analytics.traits.assembler import AssembledTraits, TraitAssembler
from app.services.analytics.traits.base import TraitContext
from app.services.analytics.traits.timing import UTC_LABEL, resolve_timezone
from app.services.analytics.traits.topic import topic_traits

REPORT_MODULES = (conversion, gap_matrix, outliers, own_performance, scorecard, trending)
TOO_FEW_OUTLIERS = "Not enough outliers in the last {days} days to find patterns."
NO_PATTERN_SUPPORT = "No pattern has 3 or more outliers across 2 or more channels in this window."


class AnalyticsEngine:
    """deterministic: every function takes as_of explicitly and sorts with a full tie-break."""

    def __init__(self, settings: AnalyticsEngineSettings) -> None:
        self.settings = settings
        self.assembler = TraitAssembler()
        self.miner = PatternMiner(settings.patterns)

    def run(self, inputs: AnalyticsInputs, as_of: datetime) -> AnalyticsResult:
        visible = inputs.visible_at(as_of)
        states, excluded = StateBuilder(self.settings, as_of).build(visible)
        book = AssessmentBook(states.values(), self.settings, as_of)
        data = EngineData(
            as_of=as_of,
            settings=self.settings,
            inputs=visible,
            states=states,
            book=book,
            traits=self._traits(visible, states, as_of),
            comment_signals=self._comment_signals(visible, states, as_of),
        )
        windows = {days: self._window(data, days) for days in self.settings.windows}
        horizon = as_of - timedelta(days=max(self.settings.windows))
        recent = [
            s for s in sorted(states.values(), key=VideoState.ordering_key) if s.published_at >= horizon
        ]
        degenerate = {vid for vid, t in data.traits.items() if t.degenerate_thumbnail}
        metrics = {
            age: {
                s.video_id: build_metrics(
                    s, age, book, as_of, (MetricFlag.degenerate_frame,) if s.video_id in degenerate else ()
                )
                for s in recent
            }
            for age in self.settings.comparison_ages()
        }
        used = {
            m.outlier.baseline_id
            for by_video in metrics.values()
            for m in by_video.values()
            if m.outlier is not None
        }
        rate_baselines = {(s.channel_id, s.segment): book.like_rate(s.channel_id, s.segment) for s in recent}
        baselines = sorted(
            [book.baselines[i].baseline for i in used] + list(rate_baselines.values()),
            key=lambda b: b.baseline_id,
        )
        recent_ids = {s.video_id for s in recent}
        lexicon_version = (
            next(iter(data.comment_signals.values())).lexicon_version if data.comment_signals else ""
        )
        return AnalyticsResult(
            client_id=visible.client.client_id,
            as_of=as_of,
            metrics=metrics,
            baselines=baselines,
            traits={vid: data.traits[vid].traits for vid in sorted(recent_ids)},
            comment_signals={
                vid: sig for vid, sig in sorted(data.comment_signals.items()) if vid in recent_ids
            },
            windows=windows,
            excluded=dict(sorted(excluded.items())),
            settings_hash=self.settings.settings_hash(),
            versions={
                "metrics": METRICS_VERSION,
                "traits": self.assembler.version,
                "thumbnail_traits": THUMBNAIL_TRAITS_VERSION,
                "lexicon": lexicon_version,
            },
        )

    def _traits(
        self, inputs: AnalyticsInputs, states: dict[str, VideoState], as_of: datetime
    ) -> dict[str, AssembledTraits]:
        matches: dict[str, list[TopicMatch]] = defaultdict(list)
        for match in inputs.topic_matches:
            matches[match.video_id].append(match)
        directness: dict[str, list[AnswerDirectness]] = defaultdict(list)
        for item in inputs.directness:
            directness[item.video_id].append(item)
        channels = {c.channel.channel_id: c for c in inputs.channels}
        built = {}
        for video_id, state in sorted(states.items()):
            scoped = channels.get(state.channel_id)
            ctx = TraitContext(
                as_of=as_of,
                client=inputs.client,
                settings=self.settings,
                segment=state.segment,
                length_band=state.length_band,
                channel_title=scoped.channel.title if scoped else state.video.channel_title,
                channel_domains=frozenset(scoped.website_domains) if scoped else frozenset(),
                is_client_video=state.channel_id == inputs.own_channel_id,
                thumbnail=inputs.thumbnails.get(video_id),
                topics=topic_traits(
                    matches[video_id], directness[video_id], self.settings.traits.topic_min_score
                ),
            )
            built[video_id] = self.assembler.build(state.video, ctx)
        return built

    def _comment_signals(
        self, inputs: AnalyticsInputs, states: dict[str, VideoState], as_of: datetime
    ) -> dict[str, CommentSignals]:
        cfg = self.settings.comments
        niche = inputs.client.niche
        lexicon = build_lexicon(niche)
        excerpts = cfg.comment_excerpts_enabled and niche not in cfg.regulated_niches
        extractor = CommentSignalExtractor(
            cfg, lexicon, inputs.buyer_questions(), inputs.client.location_terms, excerpts
        )
        scoped = {vid: comments for vid, comments in inputs.comments.items() if vid in states}
        repeated = extractor.spam.repeated_texts(scoped)
        return {
            vid: extractor.extract(states[vid].video, comments, repeated, as_of)
            for vid, comments in sorted(scoped.items())
        }

    def _window(self, data: EngineData, days: int) -> WindowAnalytics:
        age = self.settings.comparison_age(days)
        scopes = {}
        base = WindowContext(data, days, age, self.settings.ages.secondary_age_by_window.get(days), {})
        for scope, roles in SCOPE_ROLES.items():
            states = base.window_states(data.available_channel_ids(roles))
            scopes[scope] = build_scope(scope, states, age, data.book, self.settings)
        ctx = replace(base, scopes=scopes)
        lifts = {scope: self._mine(ctx, scope) for scope in scopes}
        ctx = replace(ctx, lifts=lifts)
        matrix = report_builder_registry.get("gap_matrix")().build(ctx)
        ctx = replace(ctx, gap_cells=matrix.cells)
        outlier_lists, spikes = report_builder_registry.get("outliers")().build(ctx)
        return WindowAnalytics(
            window_days=days,
            comparison_age_days=age,
            secondary_age_days=ctx.secondary_age,
            same_age_mode={scope.value: view.mode for scope, view in sorted(scopes.items())},
            lifts={scope.value: items for scope, items in sorted(lifts.items())},
            outliers=outlier_lists,
            unusual_spikes=spikes,
            scorecards=report_builder_registry.get("scorecards")().build(ctx),
            own_report=report_builder_registry.get("own_performance")().build(ctx),
            trending=report_builder_registry.get("trending")().build(ctx),
            gap_matrix=matrix,
            conversion=report_builder_registry.get("conversion")().build(ctx),
            sufficiency_messages=self._messages(ctx, lifts),
        )

    def _mine(self, ctx: WindowContext, scope: LiftScope) -> list[TraitLift]:
        view = ctx.scopes[scope]
        units = mining_units(ctx, view.assessed())
        if not units:
            return []
        request = MiningRequest(
            scope=scope,
            window_days=ctx.window_days,
            as_of=ctx.data.as_of,
            comparison_age_days=ctx.age,
            comparison_method=view.method,
        )
        return self.miner.mine(units, request)

    def _messages(self, ctx: WindowContext, lifts: dict[LiftScope, list[TraitLift]]) -> list[str]:
        messages = []
        wider = [w for w in sorted(self.settings.windows) if w > ctx.window_days]
        for scope in (LiftScope.competitor_set, LiftScope.niche):
            view = ctx.scopes[scope]
            if not view.states:
                continue
            label = scope.value.replace("_", " ")
            if not any(a.is_outlier for a in view.mining.values()):
                hint = f" Widen to {wider[0]} days?" if wider else ""
                messages.append(f"{label}: " + TOO_FEW_OUTLIERS.format(days=ctx.window_days) + hint)
            elif not any(x.status == "supported" for x in lifts[scope]):
                messages.append(f"{label}: {NO_PATTERN_SUPPORT}")
            if view.mode != "true":
                messages.append(
                    f"{label}: compared at observed ages (proxy); true same-age values cover too few videos."
                )
        if ctx.data.inputs.own_channel_id is None:
            messages.append(own_performance.NO_OWN_BASELINE)
        client = ctx.data.inputs.client
        if resolve_timezone(client.timezone, client.country) == UTC_LABEL:
            messages.append("Times shown in UTC. Set your timezone for local publish times.")
        unavailable = [
            c.channel.channel_id
            for c in ctx.data.inputs.channels
            if c.role is ChannelRole.competitor and c.channel.availability != "available"
        ]
        messages += [f"Channel {cid} is no longer available." for cid in sorted(unavailable)]
        return messages
