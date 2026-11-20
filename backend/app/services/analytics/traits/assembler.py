from dataclasses import dataclass

from app.domain.analytics import VideoTraits
from app.domain.youtube import VideoRecord
from app.services.analytics.traits import description, duration, thumbnail, timing, title, topic
from app.services.analytics.traits.base import Features, TraitContext, extractors

REGISTERED_MODULES = (description, duration, thumbnail, timing, title, topic)


@dataclass(frozen=True, slots=True)
class AssembledTraits:
    traits: VideoTraits
    features: Features
    review_flagged: frozenset[tuple[str, str]]
    malformed_links: int
    degenerate_thumbnail: bool


class TraitAssembler:
    """builds VideoTraits from the core extractors; any extra registered extractor only adds mined features."""

    def __init__(self) -> None:
        self.extractors = extractors()
        self.version = "+".join(f"{key}:{ex.version}" for key, ex in self.extractors.items())

    def build(self, video: VideoRecord, ctx: TraitContext) -> AssembledTraits:
        parts = {key: extractor.extract(video, ctx) for key, extractor in self.extractors.items()}
        features: Features = {}
        for key, extractor in self.extractors.items():
            features.update(extractor.mined(parts[key]))
        fmt, desc, thumb = parts["format"], parts["description"], parts["thumbnail"]
        traits = VideoTraits(
            video_id=video.video_id,
            traits_version=self.version,
            segment=fmt.segment,
            length_band=fmt.length_band,
            format_key=fmt.format_key,
            title=parts["title"],
            description=desc.traits,
            thumbnail=thumb.traits,
            topics=list(parts["topic"]),
            timing=parts["timing"],
        )
        flagged = frozenset(("topic", t.topic_ref_id) for t in traits.topics if t.review_flag)
        return AssembledTraits(
            traits=traits,
            features=features,
            review_flagged=flagged,
            malformed_links=desc.malformed_links,
            degenerate_thumbnail=thumb.degenerate,
        )
