from dataclasses import dataclass

from app.core.registry import trait_extractor_registry
from app.domain.common import FormatSegment, format_key
from app.domain.youtube import VideoRecord
from app.services.analytics.traits.base import Features, TraitContext


@dataclass(frozen=True, slots=True)
class FormatTraits:
    segment: FormatSegment
    length_band: str
    format_key: str


@trait_extractor_registry.register("format")
class FormatTraitExtractor:
    key = "format"
    scope = "intrinsic"
    version = "format-1"

    def extract(self, video: VideoRecord, ctx: TraitContext) -> FormatTraits:
        return FormatTraits(ctx.segment, ctx.length_band, format_key(ctx.segment, ctx.length_band))

    def mined(self, part: FormatTraits) -> Features:
        # length bands differ per segment, so the band is mined under its format key
        return {"segment": frozenset({part.segment.value}), "length_band": frozenset({part.format_key})}
