from dataclasses import dataclass

from app.core.registry import trait_extractor_registry
from app.domain.analytics import ThumbnailTraits
from app.domain.youtube import VideoRecord
from app.services.analytics.settings import ThumbnailSettings
from app.services.analytics.thumbnails.analyzer import has_text_traits
from app.services.analytics.traits.base import Features, TraitContext, flag


def is_degenerate(traits: ThumbnailTraits, settings: ThumbnailSettings) -> bool:
    return (
        traits.brightness < settings.degenerate_brightness
        or traits.rms_contrast < settings.degenerate_contrast
    )


@dataclass(frozen=True, slots=True)
class ThumbnailPart:
    traits: ThumbnailTraits | None
    degenerate: bool


@trait_extractor_registry.register("thumbnail")
class ThumbnailTraitExtractor:
    # traits are computed in the enrich stage; here they are only read
    key = "thumbnail"
    scope = "intrinsic"
    version = "thumbnail-read-1"

    def extract(self, video: VideoRecord, ctx: TraitContext) -> ThumbnailPart:
        traits = ctx.thumbnail
        degenerate = traits is not None and is_degenerate(traits, ctx.settings.thumbnails)
        return ThumbnailPart(traits=traits, degenerate=degenerate)

    def mined(self, part: ThumbnailPart) -> Features:
        # a missing or near-uniform thumbnail leaves the thumbnail population; missing is not "no text"
        traits = part.traits
        if traits is None or part.degenerate:
            return {}
        features: Features = {
            "thumbnail.brightness_band": frozenset({traits.brightness_band}),
            "thumbnail.contrast_band": frozenset({traits.contrast_band}),
            "thumbnail.colorfulness_band": frozenset({traits.colorfulness_band}),
        }
        # unread text leaves the text-trait population, the same way a missing thumbnail does
        if not has_text_traits(traits):
            return features
        features["thumbnail.text_present"] = flag(traits.text_present)
        features["thumbnail.brand_text_only"] = flag(traits.brand_text_only)
        if traits.word_count_band is not None:
            features["thumbnail.word_count_band"] = frozenset({traits.word_count_band})
        return features
