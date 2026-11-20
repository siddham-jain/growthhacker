from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol

from app.core.registry import trait_extractor_registry
from app.domain.analytics import ThumbnailTraits, TopicTrait
from app.domain.common import FormatSegment
from app.domain.youtube import VideoRecord
from app.services.analytics.inputs import ClientContext
from app.services.analytics.settings import AnalyticsEngineSettings

# mined features: trait key -> values the video has; a present key with no values means "lacks every value"
Features = dict[str, frozenset[str]]
TRUE = frozenset({"true"})
FALSE: frozenset[str] = frozenset()


def flag(value: bool) -> frozenset[str]:
    return TRUE if value else FALSE


@dataclass(frozen=True, slots=True)
class TraitContext:
    as_of: datetime
    client: ClientContext
    settings: AnalyticsEngineSettings
    segment: FormatSegment
    length_band: str
    channel_title: str = ""
    channel_domains: frozenset[str] = frozenset()
    is_client_video: bool = False
    thumbnail: ThumbnailTraits | None = None
    topics: tuple[TopicTrait, ...] = ()
    review_flagged_topics: frozenset[str] = field(default_factory=frozenset)


class TraitExtractor(Protocol):
    key: str
    scope: Literal["intrinsic", "client_context"]
    version: str

    def extract(self, video: VideoRecord, ctx: TraitContext) -> Any: ...

    def mined(self, part: Any) -> Features: ...


def extractors() -> Mapping[str, TraitExtractor]:
    return {key: factory() for key, factory in sorted(trait_extractor_registry.all().items())}
