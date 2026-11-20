from collections.abc import Iterable
from typing import Any, ClassVar, Protocol

from app.core.registry import recommender_registry
from app.domain.recommendations import RecommendationDraft, RecType, make_stable_key
from app.services.recommend.context import RecommendContext
from app.services.recommend.evidence.index import EvidenceVideo, OwnVideo

RECENT_OWN_VIDEOS = 20


class Recommender(Protocol):
    rec_type: ClassVar[RecType]
    version: ClassVar[int]

    def generate(self, ctx: RecommendContext) -> list[RecommendationDraft]: ...


def register_recommender[T: type](cls: T) -> T:
    recommender_registry.add(cls.rec_type.value, cls)
    return cls


def enabled_recommenders(disabled: Iterable[RecType] = ()) -> list[Recommender]:
    skipped = {rec_type.value for rec_type in disabled}
    return [recommender_registry.get(key)() for key in sorted(recommender_registry) if key not in skipped]


def new_draft(
    ctx: RecommendContext, recommender: Recommender, subject_key: str, **fields: Any
) -> RecommendationDraft:
    stable_key = make_stable_key(ctx.client_id, recommender.rec_type, subject_key, ctx.window_days)
    return RecommendationDraft(
        draft_id=f"d_{stable_key[:16]}",
        stable_key=stable_key,
        client_id=ctx.client_id,
        type=recommender.rec_type,
        window_days=ctx.window_days,
        as_of=ctx.as_of,
        produced_by=f"{recommender.rec_type.value}@{recommender.version}",
        **fields,
    )


def rank_by_strength(videos: Iterable[EvidenceVideo]) -> list[EvidenceVideo]:
    return sorted(
        videos,
        key=lambda video: (
            not video.is_outlier,
            -video.ratio,
            -video.published_at.timestamp(),
            video.video_id,
        ),
    )


def recent_own(ctx: RecommendContext) -> list[OwnVideo]:
    return ctx.index.own_videos[:RECENT_OWN_VIDEOS]


def own_with_traits(videos: Iterable[OwnVideo], traits: dict[str, str]) -> list[OwnVideo]:
    return [video for video in videos if all(video.traits.get(key) == value for key, value in traits.items())]
