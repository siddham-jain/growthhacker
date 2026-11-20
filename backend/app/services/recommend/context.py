from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from app.core.config import RecommendSettings
from app.domain.analytics import OwnPerformanceReport, TraitLift
from app.domain.competitors import CompetitorCandidate
from app.domain.recommendations import RecType
from app.domain.youtube import ChannelRecord, VideoRecord
from app.services.recommend.evidence.index import EvidenceIndex
from app.services.recommend.gaps import GapReport
from app.services.recommend.inputs import (
    ClientFacts,
    ClientRecommendConfig,
    FeedbackState,
    LensMode,
    PreviousCard,
)
from app.services.recommend.topics import TopicCatalog


@dataclass(frozen=True, slots=True)
class CompetitorInfo:
    channel_id: str
    name: str
    handle: str | None
    aliases: tuple[str, ...]
    domain: str | None


@dataclass(frozen=True, slots=True)
class CreatorPool:
    # 90-day upload evidence for creator candidates, read from 02's shared cache
    index_90: EvidenceIndex
    channels: dict[str, ChannelRecord]
    niche_search_hits: dict[str, int]
    candidates: tuple[CompetitorCandidate, ...]
    uploads_by_channel: dict[str, tuple[VideoRecord, ...]]


@dataclass(frozen=True, slots=True)
class RecommendContext:
    """immutable per run and window; recommenders are pure functions of it (no i/o, no wall clock)."""

    client_id: UUID
    window_days: int
    as_of: datetime
    settings: RecommendSettings
    lens_mode: LensMode
    client: ClientFacts
    catalog: TopicCatalog
    index: EvidenceIndex
    gaps: GapReport
    lifts: tuple[TraitLift, ...]
    own_report: OwnPerformanceReport | None
    own_channel_id: str | None
    competitors: tuple[CompetitorInfo, ...]
    creators: CreatorPool
    config: ClientRecommendConfig
    feedback: FeedbackState
    previous_cards: dict[str, PreviousCard]
    stale_data: bool = False
    disabled_types: frozenset[RecType] = field(default_factory=frozenset)

    @property
    def competitor_ids(self) -> list[str]:
        return [competitor.channel_id for competitor in self.competitors]
