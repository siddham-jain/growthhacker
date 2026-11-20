import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from difflib import SequenceMatcher

import regex

from app.domain.common import FormatSegment, VideoFormat
from app.domain.youtube import VideoRecord
from app.services.analytics.settings import SegmentSettings

NOT_YET_ASSESSABLE = {"upcoming", "live"}
NON_ALPHANUMERIC = regex.compile(r"[^\p{L}\p{N}]+")


def segment_of(video: VideoRecord) -> FormatSegment | None:
    # 02's format classifier is the single source; upcoming, live-now and unprocessed items are excluded
    if video.live_broadcast_content in NOT_YET_ASSESSABLE:
        return None
    if video.format in (VideoFormat.upcoming, VideoFormat.unknown):
        return None
    segment = FormatSegment(video.format.value)
    if segment is not FormatSegment.live and not video.duration_s:
        return None
    return segment


def segment_evidence(video: VideoRecord) -> dict[str, bool]:
    return {basis: True for basis in sorted(video.format_basis)}


def length_band(segment: FormatSegment, duration_s: int | None) -> str:
    seconds = duration_s or 0
    minutes = seconds / 60
    if segment is FormatSegment.short_form:
        if seconds <= 60:
            return "0-60s"
        return "61-180s" if seconds <= 180 else ">180s"
    if segment is FormatSegment.live:
        if minutes < 30:
            return "<30m"
        return "30-90m" if minutes <= 90 else ">90m"
    for upper, label in ((3, "<3m"), (8, "3-8m"), (15, "8-15m"), (30, "15-30m"), (60, "30-60m")):
        if minutes <= upper:
            return label
    return ">60m"


def normalise_title(title: str) -> str:
    return NON_ALPHANUMERIC.sub("", unicodedata.normalize("NFKC", title).casefold())


@dataclass(frozen=True, slots=True)
class DuplicateCandidate:
    video_id: str
    channel_id: str
    title: str
    duration_s: int
    published_at: datetime
    rank_views: int


@dataclass(frozen=True, slots=True)
class DuplicateGroups:
    group_of: dict[str, str] = field(default_factory=dict)
    primary_of: dict[str, str] = field(default_factory=dict)
    cross_channel_group_of: dict[str, str] = field(default_factory=dict)

    def is_secondary(self, video_id: str) -> bool:
        return video_id in self.primary_of and self.primary_of[video_id] != video_id


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, item: str) -> str:
        self.parent.setdefault(item, item)
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, left: str, right: str) -> None:
        a, b = self.find(left), self.find(right)
        if a != b:
            self.parent[max(a, b)] = min(a, b)

    def groups(self) -> dict[str, list[str]]:
        grouped: dict[str, list[str]] = {}
        for item in sorted(self.parent):
            grouped.setdefault(self.find(item), []).append(item)
        return {root: members for root, members in grouped.items() if len(members) > 1}


class DuplicateCollapser:
    def __init__(self, settings: SegmentSettings) -> None:
        self.settings = settings

    def collapse(self, candidates: Sequence[DuplicateCandidate]) -> DuplicateGroups:
        same_channel, cross_channel = _UnionFind(), _UnionFind()
        for left, right in self._matching_pairs(candidates):
            target = same_channel if left.channel_id == right.channel_id else cross_channel
            target.union(left.video_id, right.video_id)
        by_id = {c.video_id: c for c in candidates}
        group_of: dict[str, str] = {}
        primary_of: dict[str, str] = {}
        for members in same_channel.groups().values():
            group_id = f"dup_{members[0]}"
            primary = min(members, key=lambda vid: (-by_id[vid].rank_views, by_id[vid].published_at, vid))
            for member in members:
                group_of[member] = group_id
                primary_of[member] = primary
        cross = {
            member: f"xdup_{members[0]}"
            for members in cross_channel.groups().values()
            if len({by_id[m].channel_id for m in members}) > 1
            for member in members
        }
        return DuplicateGroups(group_of=group_of, primary_of=primary_of, cross_channel_group_of=cross)

    def _matching_pairs(
        self, candidates: Sequence[DuplicateCandidate]
    ) -> list[tuple[DuplicateCandidate, DuplicateCandidate]]:
        ordered = sorted(candidates, key=lambda c: (c.duration_s, c.video_id))
        titles = {c.video_id: normalise_title(c.title) for c in ordered}
        window = timedelta(days=self.settings.dup_window_days)
        pairs = []
        for index, left in enumerate(ordered):
            for right in ordered[index + 1 :]:
                if right.duration_s - left.duration_s > self.settings.dup_duration_tol_s:
                    break
                if abs(right.published_at - left.published_at) > window:
                    continue
                a, b = titles[left.video_id], titles[right.video_id]
                if a and b and SequenceMatcher(None, a, b).ratio() * 100 >= self.settings.dup_title_ratio:
                    pairs.append((left, right))
        return pairs
