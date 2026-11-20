import hashlib
import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Literal

import numpy as np

from app.domain.analytics import AgeMethod, ChannelBaseline
from app.domain.common import FormatSegment
from app.services.analytics.ages import views_at_exact_age
from app.services.analytics.settings import AnalyticsEngineSettings
from app.services.analytics.snapshots import CleanSeries

MAD_SCALE = 1.4826


class AgeFamily(StrEnum):
    # values from different families are never placed in one ratio
    data_api = "data_api"
    owner = "owner"
    proxy = "proxy"


TRUE_FAMILIES = (AgeFamily.owner, AgeFamily.data_api)


def family_of(method: AgeMethod) -> AgeFamily | None:
    return {
        AgeMethod.observed: AgeFamily.data_api,
        AgeMethod.interpolated: AgeFamily.data_api,
        AgeMethod.owner_analytics: AgeFamily.owner,
        AgeMethod.age_band_proxy: AgeFamily.proxy,
    }.get(method)


@dataclass(frozen=True, slots=True)
class AgeValue:
    value: int
    method: AgeMethod
    observed_age_days: float
    source_oldest_at: datetime


@dataclass(frozen=True, slots=True)
class VideoFacts:
    """what baselines and outliers need to know about one assessable video."""

    video_id: str
    channel_id: str
    segment: FormatSegment
    published_at: datetime
    language: str | None
    baseline_eligible: bool
    series: CleanSeries
    views_now: int
    latest_age_days: float
    values: dict[tuple[int, AgeFamily], AgeValue] = field(default_factory=dict)
    like_rate: float | None = None
    engagement_rate: float | None = None
    engagement_basis: str = "none"

    def value(self, age: int, family: AgeFamily) -> AgeValue | None:
        return self.values.get((age, family))


@dataclass(frozen=True, slots=True)
class Summary:
    median: float
    mean: float
    trimmed_mean_10: float
    p25: float
    p75: float
    mad_log10: float


def summarise(values: Sequence[float]) -> Summary:
    if not values:
        return Summary(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    data = np.sort(np.asarray(values, dtype=float))
    median = float(np.median(data))
    trim = int(math.floor(0.1 * len(data)))
    trimmed = data[trim : len(data) - trim] if len(data) - 2 * trim > 0 else data
    logs = np.log10(np.maximum(data, 1.0))
    mad = float(MAD_SCALE * np.median(np.abs(logs - math.log10(max(median, 1.0)))))
    return Summary(
        median=median,
        mean=float(np.mean(data)),
        trimmed_mean_10=float(np.mean(trimmed)),
        p25=float(np.percentile(data, 25)),
        p75=float(np.percentile(data, 75)),
        mad_log10=mad,
    )


def baseline_id(*parts: object) -> str:
    digest = hashlib.sha256("|".join(str(part) for part in parts).encode()).hexdigest()
    return f"bl_{digest[:16]}"


FallbackLevel = Literal["trailing", "non_trailing", "widened", "none"]


@dataclass(frozen=True, slots=True)
class BuiltBaseline:
    baseline: ChannelBaseline
    widened: bool
    source_oldest_at: datetime | None


class BaselineBuilder:
    """per-video trailing baselines with the fallback ladder; no cross-segment or cross-family fallback."""

    def __init__(
        self, facts: Iterable[VideoFacts], settings: AnalyticsEngineSettings, as_of: datetime
    ) -> None:
        self.settings = settings
        self.as_of = as_of
        self._groups: dict[tuple[str, FormatSegment], list[VideoFacts]] = {}
        for fact in sorted(facts, key=lambda f: (f.published_at, f.video_id)):
            self._groups.setdefault((fact.channel_id, fact.segment), []).append(fact)
        self._cache: dict[tuple[str, int, AgeFamily], BuiltBaseline] = {}
        self._split_cache: dict[tuple[str, FormatSegment, int, AgeFamily], bool] = {}

    def peers(self, fact: VideoFacts) -> list[VideoFacts]:
        return [
            other
            for other in self._groups.get((fact.channel_id, fact.segment), [])
            if other.video_id != fact.video_id and other.baseline_eligible
        ]

    def for_video(self, fact: VideoFacts, age: int, family: AgeFamily) -> BuiltBaseline:
        key = (fact.video_id, age, family)
        if key not in self._cache:
            builder = self._proxy if family is AgeFamily.proxy else self._true
            self._cache[key] = builder(fact, age, family)
        return self._cache[key]

    def _true(self, fact: VideoFacts, age: int, family: AgeFamily) -> BuiltBaseline:
        cfg = self.settings.baselines
        candidates = [u for u in self.peers(fact) if u.value(age, family) is not None]
        candidates = self._language_restricted(fact, candidates, age, family)
        lookback = timedelta(days=cfg.lookback_days)
        widened = timedelta(days=cfg.widened_lookback_days)
        ladder: list[tuple[FallbackLevel, list[VideoFacts]]] = [
            (
                "trailing",
                sorted(
                    (
                        u
                        for u in candidates
                        if fact.published_at - lookback <= u.published_at < fact.published_at
                    ),
                    key=lambda u: (u.published_at, u.video_id),
                    reverse=True,
                )[: cfg.lookback_videos],
            ),
            ("non_trailing", self._nearest(fact, candidates, lookback, cfg.lookback_videos)),
            ("widened", self._nearest(fact, candidates, widened, cfg.widened_lookback_videos)),
        ]
        for level, members in ladder:
            if len(members) >= cfg.min_baseline_n:
                return self._make(fact, age, family, members, level, lambda u: u.value(age, family).value)
        return self._make(fact, age, family, ladder[-1][1], "none", lambda u: u.value(age, family).value)

    @staticmethod
    def _nearest(
        fact: VideoFacts, candidates: list[VideoFacts], span: timedelta, limit: int
    ) -> list[VideoFacts]:
        inside = [u for u in candidates if abs(u.published_at - fact.published_at) <= span]
        return sorted(
            inside, key=lambda u: (abs((u.published_at - fact.published_at).total_seconds()), u.video_id)
        )[:limit]

    def _language_restricted(
        self, fact: VideoFacts, candidates: list[VideoFacts], age: int, family: AgeFamily
    ) -> list[VideoFacts]:
        cfg = self.settings.baselines
        if not cfg.baseline_split_by_language or fact.language is None:
            return candidates
        key = (fact.channel_id, fact.segment, age, family)
        if key not in self._split_cache:
            self._split_cache[key] = self._should_split(fact, age, family)
        if not self._split_cache[key]:
            return candidates
        return [u for u in candidates if u.language == fact.language]

    def _should_split(self, fact: VideoFacts, age: int, family: AgeFamily) -> bool:
        cfg = self.settings.baselines
        by_language: dict[str, list[int]] = {}
        for member in self._groups.get((fact.channel_id, fact.segment), []):
            value = member.value(age, family)
            if member.baseline_eligible and member.language and value is not None:
                by_language.setdefault(member.language, []).append(value.value)
        medians = [float(np.median(v)) for v in by_language.values() if len(v) >= cfg.min_baseline_n]
        if len(medians) < 2:
            return False
        return max(medians) >= cfg.language_split_min_ratio * max(min(medians), 1.0)

    def _proxy(self, fact: VideoFacts, age: int, family: AgeFamily) -> BuiltBaseline:
        own = fact.value(age, AgeFamily.proxy)
        target_age = own.observed_age_days if own else fact.latest_age_days
        cfg, proxy = self.settings.baselines, self.settings.proxy
        lookback = timedelta(days=cfg.lookback_days)
        pool = [u for u in self.peers(fact) if abs(u.published_at - fact.published_at) <= lookback]
        kappa = proxy.kappa(target_age)
        widenings = 0
        while True:
            peers = self._proxy_peers(pool, target_age, kappa)
            if len(peers) >= cfg.min_baseline_n or widenings >= proxy.proxy_max_widenings:
                break
            kappa *= proxy.widen_factor
            widenings += 1
        level: FallbackLevel = "widened" if widenings else "non_trailing"
        if len(peers) < cfg.min_baseline_n:
            level = "none"
        values = {u.video_id: value for u, value, _ in peers}
        band = (target_age / kappa, target_age * kappa)
        return self._make(
            fact,
            age,
            family,
            [u for u, _, _ in peers],
            level,
            lambda u: values[u.video_id],
            age_band=band,
            widened=widenings > 0,
        )

    def _proxy_peers(
        self, pool: list[VideoFacts], target_age: float, kappa: float
    ) -> list[tuple[VideoFacts, float, float]]:
        gap = self.settings.ages.max_gap_days_for(target_age)
        found: list[tuple[VideoFacts, float, float]] = []
        for peer in pool:
            exact = views_at_exact_age(peer.series, target_age, gap)
            if exact is not None:
                found.append((peer, exact, 0.0))
            elif (
                target_age / kappa <= peer.latest_age_days <= target_age * kappa and peer.latest_age_days > 0
            ):
                found.append((peer, float(peer.views_now), abs(math.log(peer.latest_age_days / target_age))))
        found.sort(key=lambda item: (item[2], -item[0].published_at.timestamp(), item[0].video_id))
        return found[: self.settings.proxy.proxy_max_peers]

    def _make(
        self,
        fact: VideoFacts,
        age: int,
        family: AgeFamily,
        members: Sequence[VideoFacts],
        level: FallbackLevel,
        value_of: Callable[[VideoFacts], float],
        *,
        age_band: tuple[float, float] | None = None,
        widened: bool = False,
    ) -> BuiltBaseline:
        values = [float(value_of(u)) for u in members]
        stats = summarise(values)
        method = self._representative_method(family, members, age)
        ids = [u.video_id for u in members]
        sufficient = level != "none" and len(members) >= self.settings.baselines.min_baseline_n
        oldest = [
            u.value(age, family).source_oldest_at
            for u in members
            if family is not AgeFamily.proxy and u.value(age, family) is not None
        ] or [u.series.latest.captured_at for u in members if u.series.latest is not None]
        baseline = ChannelBaseline(
            baseline_id=baseline_id(
                fact.channel_id, fact.segment, "views_at_age", age, method, fact.video_id, self.as_of, *ids
            ),
            channel_id=fact.channel_id,
            segment=fact.segment,
            metric="views_at_age",
            age_days=age,
            method=method,
            age_band=age_band,
            as_of=self.as_of,
            anchor_video_id=fact.video_id,
            median=stats.median,
            mean=stats.mean,
            trimmed_mean_10=stats.trimmed_mean_10,
            p25=stats.p25,
            p75=stats.p75,
            mad_log10=stats.mad_log10,
            n=len(members),
            video_ids=ids,
            fallback_level=level,
            sufficient=sufficient,
        )
        return BuiltBaseline(baseline=baseline, widened=widened, source_oldest_at=min(oldest, default=None))

    @staticmethod
    def _representative_method(family: AgeFamily, members: Sequence[VideoFacts], age: int) -> AgeMethod:
        if family is AgeFamily.proxy:
            return AgeMethod.age_band_proxy
        if family is AgeFamily.owner:
            return AgeMethod.owner_analytics
        methods = {u.value(age, family).method for u in members if u.value(age, family) is not None}
        return AgeMethod.interpolated if AgeMethod.interpolated in methods else AgeMethod.observed

    def rate_baseline(
        self, channel_id: str, segment: FormatSegment, metric: Literal["like_rate", "engagement_rate"]
    ) -> ChannelBaseline:
        members = [
            f
            for f in self._groups.get((channel_id, segment), [])
            if f.baseline_eligible and getattr(f, metric) is not None
        ]
        if metric == "engagement_rate":
            bases = sorted({f.engagement_basis for f in members})
            dominant = max(
                bases, key=lambda b: (sum(f.engagement_basis == b for f in members), b), default=None
            )
            members = [f for f in members if f.engagement_basis == dominant]
        values = [float(getattr(f, metric)) for f in members]
        stats = summarise(values)
        ids = [f.video_id for f in members]
        return ChannelBaseline(
            baseline_id=baseline_id(channel_id, segment, metric, self.as_of, *ids),
            channel_id=channel_id,
            segment=segment,
            metric=metric,
            age_days=None,
            method=AgeMethod.observed,
            as_of=self.as_of,
            median=stats.median,
            mean=stats.mean,
            trimmed_mean_10=stats.trimmed_mean_10,
            p25=stats.p25,
            p75=stats.p75,
            mad_log10=0.0,
            n=len(members),
            video_ids=ids,
            fallback_level="non_trailing" if members else "none",
            sufficient=len(members) >= self.settings.baselines.min_baseline_n,
        )


class BaselineStore:
    """read access to built baselines by channel, segment, age and as_of (consumed by 08)."""

    def __init__(self, baselines: Iterable[ChannelBaseline]) -> None:
        self._items = sorted(baselines, key=lambda b: (b.as_of, b.baseline_id))

    def get(
        self, channel_id: str, segment: FormatSegment, age_days: int, as_of: datetime
    ) -> ChannelBaseline | None:
        matches = [
            b
            for b in self._items
            if b.channel_id == channel_id
            and b.segment == segment
            and b.metric == "views_at_age"
            and b.age_days == age_days
            and b.as_of <= as_of
            and b.sufficient
        ]
        return matches[-1] if matches else None
