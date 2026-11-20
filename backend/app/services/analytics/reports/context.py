from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol, TypeVar

from app.core.registry import Registry
from app.domain.analytics import (
    AgeMethod,
    CommentSignals,
    GapCell,
    LiftScope,
    OutlierAssessment,
    OutlierTier,
    TraitLift,
)
from app.domain.common import ChannelRole
from app.services.analytics.baselines import AgeFamily
from app.services.analytics.inputs import AnalyticsInputs, ScopedChannel
from app.services.analytics.result import SameAgeMode
from app.services.analytics.settings import AnalyticsEngineSettings
from app.services.analytics.state import AssessmentBook, VideoState, has_true_value
from app.services.analytics.traits.assembler import AssembledTraits

R = TypeVar("R", covariant=True)

SCOPE_ROLES: dict[LiftScope, tuple[ChannelRole, ...]] = {
    LiftScope.own: (ChannelRole.own,),
    LiftScope.competitor_set: (ChannelRole.competitor,),
    LiftScope.niche: (ChannelRole.competitor, ChannelRole.niche),
}


@dataclass(frozen=True, slots=True)
class EngineData:
    as_of: datetime
    settings: AnalyticsEngineSettings
    inputs: AnalyticsInputs
    states: dict[str, VideoState]
    book: AssessmentBook
    traits: dict[str, AssembledTraits]
    comment_signals: dict[str, CommentSignals]

    def channel(self, channel_id: str) -> ScopedChannel | None:
        return next((c for c in self.inputs.channels if c.channel.channel_id == channel_id), None)

    def available_channel_ids(self, roles: Sequence[ChannelRole]) -> set[str]:
        return {
            c.channel.channel_id
            for c in self.inputs.channels
            if c.role in roles and c.channel.availability == "available"
        }

    def published_between(self, start: datetime, end: datetime, channel_ids: set[str]) -> list[VideoState]:
        found = [
            s for s in self.states.values() if start <= s.published_at <= end and s.channel_id in channel_ids
        ]
        return sorted(found, key=VideoState.ordering_key)


@dataclass(frozen=True, slots=True)
class ScopeView:
    scope: LiftScope
    mode: SameAgeMode
    family: AgeFamily | None
    states: tuple[VideoState, ...]
    mining: dict[str, OutlierAssessment]

    @property
    def method(self) -> AgeMethod:
        if self.family is AgeFamily.proxy:
            return AgeMethod.age_band_proxy
        methods = Counter(a.method for a in self.mining.values())
        return min(methods, key=lambda m: (-methods[m], m.value)) if methods else AgeMethod.observed

    def assessed(self) -> list[tuple[VideoState, OutlierAssessment]]:
        return [(s, self.mining[s.video_id]) for s in self.states if s.video_id in self.mining]


@dataclass(frozen=True, slots=True)
class WindowContext:
    data: EngineData
    window_days: int
    age: int
    secondary_age: int | None
    scopes: dict[LiftScope, ScopeView]
    lifts: dict[LiftScope, list[TraitLift]] = field(default_factory=dict)
    gap_cells: dict[str, dict[str, GapCell]] = field(default_factory=dict)

    @property
    def settings(self) -> AnalyticsEngineSettings:
        return self.data.settings

    @property
    def start(self) -> datetime:
        return self.data.as_of - timedelta(days=self.window_days)

    def display(self, state: VideoState) -> OutlierAssessment | None:
        return self.data.book.best(state, self.age)

    def window_states(self, channel_ids: set[str], *, include_secondary: bool = False) -> list[VideoState]:
        states = self.data.published_between(self.start, self.data.as_of, channel_ids)
        return [s for s in states if include_secondary or not s.duplicate_secondary]


def choose_mode(
    states: Sequence[VideoState], age: int, book: AssessmentBook, settings: AnalyticsEngineSettings
) -> SameAgeMode:
    # true mode needs 80% true coverage and a sufficient true baseline behind every outlier candidate
    judgeable = [s for s in states if s.ages[age].method is not AgeMethod.pending]
    if not judgeable:
        return "proxy"
    true_states = [s for s in judgeable if has_true_value(s, age)]
    coverage = len(true_states) / len(judgeable)
    candidates = [
        book.true_assessment(s, age)
        for s in true_states
        if (s.facts.value(age, AgeFamily.data_api) or s.facts.value(age, AgeFamily.owner)).value
        >= settings.outliers.min_outlier_views
    ]
    channels_ok = all(a is not None and a.tier is not OutlierTier.insufficient for a in candidates)
    if coverage >= settings.proxy.true_mode_min_coverage and channels_ok:
        return "true"
    return "mixed_by_video" if true_states else "proxy"


def build_scope(
    scope: LiftScope,
    states: Sequence[VideoState],
    age: int,
    book: AssessmentBook,
    settings: AnalyticsEngineSettings,
) -> ScopeView:
    mode = choose_mode(states, age, book, settings)
    mining: dict[str, OutlierAssessment] = {}
    for state in states:
        assessed = (
            book.true_assessment(state, age) if mode == "true" else book.assess(state, age, AgeFamily.proxy)
        )
        if assessed is not None and assessed.tier is not OutlierTier.insufficient:
            mining[state.video_id] = assessed
    family = None if mode == "true" else AgeFamily.proxy
    return ScopeView(scope=scope, mode=mode, family=family, states=tuple(states), mining=mining)


class ReportBuilder(Protocol[R]):
    kind: str

    def build(self, ctx: WindowContext) -> R: ...


report_builder_registry: Registry[type] = Registry("report_builder_registry")
