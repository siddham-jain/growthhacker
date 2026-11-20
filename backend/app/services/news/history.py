import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.domain.recommendations import ExpectedRange
from app.services.news.context import NewsClientContext
from app.services.news.text import contains_phrase
from app.services.news.versions import version_tokens

COMPARISON_AGE_DAYS = 7
REACTION_WINDOW = timedelta(days=7)
LAUNCH_TERMS = ("launch", "new", "released", "approved", "update")


@dataclass(frozen=True)
class PastNewsEntity:
    entity: str
    first_seen_at: datetime


@dataclass(frozen=True)
class NewsHistory:
    ratios: list[float]
    heuristic: bool
    expected_range: ExpectedRange


def _fmt(ratio: float) -> str:
    return f"{ratio:.1f}x"


class NewsReactiveHistory:
    """the client's own news-reaction videos vs its own day-7 baseline; never other channels' ratios."""

    def __init__(self, min_videos: int, lookback_days: int) -> None:
        self._min_videos = min_videos
        self._lookback = timedelta(days=lookback_days)

    def _reaction_ratios(
        self, ctx: NewsClientContext, past: Sequence[PastNewsEntity], now: datetime
    ) -> tuple[list[float], bool]:
        videos = [v for v in ctx.own_videos if v.published_at >= now - self._lookback]
        matched: dict[str, float] = {}
        for video in videos:
            text = f"{video.title}\n{video.description}"
            for entity in past:
                in_window = (
                    entity.first_seen_at <= video.published_at <= entity.first_seen_at + REACTION_WINDOW
                )
                if (
                    in_window
                    and contains_phrase(text, entity.entity)
                    and video.video_id in ctx.own_day7_ratios
                ):
                    matched[video.video_id] = ctx.own_day7_ratios[video.video_id]
        if matched:
            return list(matched.values()), False
        names = [n for e in ctx.freshness.watch_entities for n in (e.name, *e.aliases)]
        for video in videos:
            title = video.title
            named = any(contains_phrase(title, name) for name in names)
            launch_like = bool(version_tokens(title, names)) or any(
                contains_phrase(title, t) for t in LAUNCH_TERMS
            )
            if named and launch_like and video.video_id in ctx.own_day7_ratios:
                matched[video.video_id] = ctx.own_day7_ratios[video.video_id]
        return list(matched.values()), True

    def estimate(self, ctx: NewsClientContext, past: Sequence[PastNewsEntity], now: datetime) -> NewsHistory:
        if ctx.own_channel_id is None:
            return NewsHistory([], False, self._insufficient("insufficient history (no channel connected)"))
        ratios, heuristic = self._reaction_ratios(ctx, past, now)
        label = " (heuristic match)" if heuristic and ratios else ""
        if len(ratios) >= self._min_videos:
            quartiles = statistics.quantiles(sorted(ratios), n=4, method="inclusive")
            low, high = round(quartiles[0], 2), round(quartiles[2], 2)
            display = (
                f"{_fmt(low)}–{_fmt(high)} (indicative, from your {len(ratios)} news-reaction videos){label}"
            )
            return NewsHistory(ratios, heuristic, self._own(low, high, len(ratios), display))
        if ratios:
            listed = " and ".join(_fmt(r) for r in sorted(ratios))
            display = f"limited history: your news-reaction videos ran {listed} your baseline{label}"
            return NewsHistory(ratios, heuristic, self._own(min(ratios), max(ratios), len(ratios), display))
        return NewsHistory(
            [],
            heuristic,
            self._insufficient("insufficient history — no past news-reaction videos; treat as an experiment"),
        )

    def _own(self, low: float, high: float, n: int, display: str) -> ExpectedRange:
        return ExpectedRange(
            low=round(low, 2),
            high=round(high, 2),
            basis="own",
            n_own=n,
            comparison_age_days=COMPARISON_AGE_DAYS,
            display=display,
        )

    def _insufficient(self, display: str) -> ExpectedRange:
        return ExpectedRange(
            low=None,
            high=None,
            basis="insufficient",
            comparison_age_days=COMPARISON_AGE_DAYS,
            display=display,
        )
