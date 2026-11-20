from collections.abc import Sequence
from datetime import timedelta

from app.core.config import TopicEngineSettings
from app.domain.profile import BusinessProfile, BuyerQuestion
from app.domain.topics import PreparedVideoText, TopicUniverse
from app.services.topics.lexicon import load_lexicons
from app.services.topics.matching.base import MatchContext, MatcherResult
from app.services.topics.matching.lexical import LexicalMatcher
from app.services.topics.pipeline import VideoMeta
from app.services.topics.universe.builder import BuiltUniverse, TopicUniverseBuilder, UniverseInputs
from app.services.topics.universe.expansion.base import CorpusFrequencies
from tests.topics.fixtures import CHANNEL, NOW


async def universe_for(
    profile: BusinessProfile,
    questions: Sequence[BuyerQuestion] = (),
    settings: TopicEngineSettings | None = None,
) -> BuiltUniverse:
    builder = TopicUniverseBuilder(
        settings or TopicEngineSettings(), load_lexicons(), engine_version="e-test", embedder_model_ids={}
    )
    return await builder.build(UniverseInputs(profile=profile, buyer_questions=questions), built_at=NOW)


def label_of(universe: TopicUniverse) -> dict[str, str]:
    return {ref.id: ref.label for ref in universe.refs}


async def lexical(
    built: BuiltUniverse,
    videos: Sequence[PreparedVideoText],
    settings: TopicEngineSettings | None = None,
    corpus: CorpusFrequencies | None = None,
) -> dict[str, dict[str, MatcherResult]]:
    """video_id -> ref label -> lexical result"""
    ctx = MatchContext(
        universe=built.universe,
        settings=settings or TopicEngineSettings(),
        lexicons=load_lexicons(),
        non_topical_terms=built.non_topical_terms,
        corpus=corpus or CorpusFrequencies(),
    )
    results = await LexicalMatcher().score(videos, ctx)
    labels = label_of(built.universe)
    grouped: dict[str, dict[str, MatcherResult]] = {}
    for (video_id, ref_id), result in results.items():
        grouped.setdefault(video_id, {})[labels[ref_id]] = result
    return grouped


def meta_for(videos: Sequence[PreparedVideoText], **overrides) -> dict[str, VideoMeta]:
    return {
        video.video_id: VideoMeta(
            video_id=video.video_id,
            channel_id=CHANNEL,
            published_at=NOW,
            observed_at=NOW,
            expires_at=NOW + timedelta(days=28),
            **overrides,
        )
        for video in videos
    }
