from typing import Protocol

from app.core.registry import Registry, UnknownRegistrationError
from app.domain.recommendations import MatchSpec, RecType
from app.evaluation.backtest.models import CorpusVideo


class UnmatchableSpecType(LookupError):
    """a rec type without a registered matcher is excluded from the backtest with a note (edge case 66)."""


class SpecMatcher(Protocol):
    def matches(self, spec: MatchSpec, video: CorpusVideo) -> bool: ...


matcher_registry: Registry[SpecMatcher] = Registry("backtest_matcher_registry")


def trait_text(value: bool | str) -> str:
    # 06 mines boolean traits as "true"/"false"
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


class ConstraintMatcher:
    """true iff every constraint in the spec holds; topic ids are any-of (plan 12 §5.1.2 step 6)."""

    def matches(self, spec: MatchSpec, video: CorpusVideo) -> bool:
        if spec.topic_ids and not video.topic_ids.intersection(spec.topic_ids):
            return False
        if spec.format is not None and video.segment != spec.format.value:
            return False
        if spec.length_band is not None and video.traits.get("length_band") != spec.length_band:
            return False
        if spec.channel_id is not None and video.channel_id != spec.channel_id:
            return False
        for prefix, traits in (("title", spec.title_traits), ("thumbnail", spec.thumbnail_traits)):
            for key, value in traits.items():
                if video.traits.get(f"{prefix}.{key}") != trait_text(value):
                    return False
        return True


_CONSTRAINTS = ConstraintMatcher()
for _rec_type in (
    RecType.video_idea,
    RecType.title_pattern,
    RecType.thumbnail_pattern,
    RecType.format,
    RecType.creator,
):
    matcher_registry.add(_rec_type.value, _CONSTRAINTS)


def matcher_for(rec_type: RecType) -> SpecMatcher:
    try:
        return matcher_registry.get(rec_type.value)
    except UnknownRegistrationError:
        raise UnmatchableSpecType(f"no backtest matcher for '{rec_type.value}' specs") from None


def matches(spec: MatchSpec, video: CorpusVideo) -> bool:
    return matcher_for(spec.rec_type).matches(spec, video)
