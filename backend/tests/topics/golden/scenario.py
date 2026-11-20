from dataclasses import dataclass
from datetime import timedelta
from types import ModuleType

from app.adapters.embeddings.cache import EmbeddingCache
from app.adapters.embeddings.router import EmbedderRouter
from app.core.config import TopicEngineSettings
from app.domain.topics import PreparedVideoText, TopicUniverse
from app.ports.embeddings import Embedder
from app.services.topics.calibration.background import load_background
from app.services.topics.corpus import corpus_frequencies
from app.services.topics.lexicon import load_lexicons
from app.services.topics.llm import UnlimitedSpend
from app.services.topics.matching.base import MatcherDeps, matcher_registry
from app.services.topics.matching.combine import build_combiner
from app.services.topics.matching.semantic import RefVectorStore
from app.services.topics.pipeline import MatchPipeline, PipelineOutput, VideoMeta
from app.services.topics.universe.builder import BuiltUniverse, TopicUniverseBuilder, UniverseInputs
from app.services.topics.universe.expansion.llm_proposer import LLMAliasProposer
from tests.topics.fixtures import CHANNEL, NOW, FakeLLM, judge_by_label, prepare, video_text

SCRIPTS = ("Latn", "Deva")


@dataclass
class ScenarioResult:
    built: BuiltUniverse
    videos: list[PreparedVideoText]
    output: PipelineOutput
    llm: FakeLLM

    @property
    def universe(self) -> TopicUniverse:
        return self.built.universe

    def accepted_labels(self, key: str, fixture: ModuleType) -> set[str]:
        labels = {ref.id: ref.label for ref in self.universe.refs}
        video = self.videos[[row[0] for row in fixture.VIDEOS].index(key)]
        return {
            labels[match.topic_ref_id]
            for match in self.output.matches
            if match.video_id == video.video_id and match.decision == "accepted"
        }

    def video(self, key: str, fixture: ModuleType) -> PreparedVideoText:
        return self.videos[[row[0] for row in fixture.VIDEOS].index(key)]


async def run_scenario(
    fixture: ModuleType, embedder: Embedder | None, settings: TopicEngineSettings | None = None
) -> ScenarioResult:
    settings = settings or TopicEngineSettings()
    lexicons = load_lexicons()
    llm = FakeLLM(judge=judge_by_label(fixture.ADJUDICATION_RULES), proposals=fixture.PROPOSALS)
    cache = EmbeddingCache(None)
    router = (
        EmbedderRouter({"minilm": embedder}, {script: ["minilm"] for script in SCRIPTS}, "minilm")
        if embedder
        else None
    )

    async def encode(texts):
        return await cache.embed(embedder, list(texts), "query")

    builder = TopicUniverseBuilder(
        settings,
        lexicons,
        engine_version="golden",
        embedder_model_ids=router.model_ids() if router else {},
        encoder=encode if embedder else None,
        proposer=LLMAliasProposer(llm, settings, UnlimitedSpend()),
    )
    built = await builder.build(
        UniverseInputs(profile=fixture.PROFILE, buyer_questions=fixture.QUESTIONS), built_at=NOW
    )
    deps = MatcherDeps(
        settings=settings,
        lexicons=lexicons,
        router=router,
        embedding_cache=cache,
        ref_vectors=RefVectorStore(cache, load_background()),
    )
    pipeline = MatchPipeline(
        settings,
        lexicons,
        [matcher_registry.get(name)(deps) for name in matcher_registry],
        build_combiner(settings),
        engine_version="golden",
        llm=llm,
        guard=UnlimitedSpend(),
        model_key="claude-haiku-4-5",
        locations=fixture.PROFILE.locations.cities,
    )
    videos = [
        prepare(video_text(title, description, tags, key=key, is_short=key.startswith("short")), settings)
        for key, title, description, tags, _ in fixture.VIDEOS
    ]
    meta = {
        video.video_id: VideoMeta(
            video_id=video.video_id,
            channel_id=CHANNEL,
            published_at=NOW,
            observed_at=NOW,
            expires_at=NOW + timedelta(days=28),
            is_short=row[0].startswith("short"),
        )
        for video, row in zip(videos, fixture.VIDEOS, strict=True)
    }
    output = await pipeline.run(
        built.universe,
        built.non_topical_terms,
        videos,
        meta,
        corpus_frequencies(videos),
        mode="cold",
        now=NOW,
    )
    return ScenarioResult(built=built, videos=videos, output=output, llm=llm)
