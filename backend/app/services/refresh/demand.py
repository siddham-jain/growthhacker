from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

from app.core.config import RefreshSettings
from app.domain.common import ChannelRole
from app.services.refresh.inputs import ReferenceCandidate
from app.services.refresh.text import KeywordPresence, normalise


@dataclass(frozen=True, slots=True)
class DemandEvidence:
    keyword: str
    refs: tuple[ReferenceCandidate, ...]
    outlier_refs: tuple[ReferenceCandidate, ...]
    owner_search_hit: bool
    demand: float

    @property
    def channels(self) -> int:
        return len({ref.video.channel_id for ref in self.refs})

    @property
    def outlier_channels(self) -> int:
        return len({ref.video.channel_id for ref in self.outlier_refs})


class DemandEvidenceCollector:
    """youtube-only demand evidence: recent niche videos inside the window that use the keyword."""

    def __init__(
        self,
        pool: Sequence[ReferenceCandidate],
        *,
        own_channel_id: str | None,
        window_start: datetime,
        settings: RefreshSettings,
    ) -> None:
        self.settings = settings
        self._pool = [
            (ref, KeywordPresence(f"{ref.video.title}\n{ref.video.description}"))
            for ref in pool
            if ref.role is not ChannelRole.own
            and ref.video.channel_id != own_channel_id
            and ref.video.published_at >= window_start
            and ref.video.live_broadcast_content == "none"
        ]
        self._cache: dict[tuple[str, tuple[str, ...], str | None, bool], DemandEvidence] = {}

    def collect(
        self,
        keyword: str,
        aliases: Sequence[str] = (),
        owner_terms: Sequence[str] = (),
        topic_ref_id: str | None = None,
        extra_filter: Callable[[ReferenceCandidate], bool] | None = None,
    ) -> DemandEvidence:
        phrases = (keyword, *aliases)
        key = (
            normalise(keyword),
            tuple(sorted(normalise(alias) for alias in aliases)),
            topic_ref_id,
            bool(owner_terms),
        )
        if extra_filter is None and key in self._cache:
            return self._cache[key]
        refs = tuple(
            ref
            for ref, presence in self._pool
            if presence.contains_any(phrases)
            and (topic_ref_id is None or topic_ref_id in ref.matched_topic_ids)
            and (extra_filter is None or extra_filter(ref))
        )
        outliers = tuple(
            ref for ref in refs if ref.metrics.outlier is not None and ref.metrics.outlier.is_outlier
        )
        min_refs = self.settings.min_refs
        channels = len({ref.video.channel_id for ref in refs})
        demand = 0.6 * min(1.0, len(outliers) / min_refs) + 0.4 * min(1.0, channels / 3)
        owner_hit = any(KeywordPresence(term).contains_any(phrases) for term in owner_terms)
        if owner_hit:
            demand = min(1.0, demand + 0.3)
        evidence = DemandEvidence(
            keyword=keyword,
            refs=refs,
            outlier_refs=outliers,
            owner_search_hit=owner_hit,
            demand=round(demand, 4),
        )
        if extra_filter is None:
            self._cache[key] = evidence
        return evidence
