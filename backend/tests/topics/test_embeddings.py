import re
from pathlib import Path

import numpy as np
import pytest

from app.adapters.embeddings import build_router
from app.adapters.embeddings.cache import EmbeddingCache
from app.adapters.embeddings.fastembed_embedder import FastEmbedEmbedder, l2_normalise
from app.adapters.embeddings.null_embedder import NullEmbedder
from app.adapters.embeddings.router import EmbedderRouter
from app.core.config import EmbeddingModelSettings, EmbeddingSettings
from app.ports.embeddings import EmbedderUnavailable
from tests.topics.fixtures import HashEmbedder

MODEL = EmbeddingModelSettings(name="test/model", revision="r1", dim=4, query_prefix="query: ")
TOPICS_ROOT = Path(__file__).resolve().parents[2] / "app" / "services" / "topics"


class StubModel:
    # mimics fastembed returning unnormalised rows (minilm gave dot products near 20)
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.inputs: list[str] = []

    def embed(self, texts, batch_size=64):
        self.inputs += list(texts)
        for index, _ in enumerate(texts):
            yield np.array([3.0 + index, 4.0, 0.0, 12.0], dtype=np.float32)


def embedder(factory) -> FastEmbedEmbedder:
    return FastEmbedEmbedder(
        MODEL,
        cache_dir=Path("/tmp/none"),
        threads=1,
        batch_size=8,
        local_files_only=True,
        model_factory=factory,
    )


def test_vectors_are_always_l2_normalised() -> None:
    model = StubModel()
    vectors = embedder(lambda **kwargs: model).embed(["a", "b", "c"], "query")
    assert vectors.dtype == np.float32
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=1e-5)
    assert model.inputs == ["query: a", "query: b", "query: c"]
    assert l2_normalise(np.zeros((1, 3))).tolist() == [[0.0, 0.0, 0.0]]


def test_model_load_failure_is_embedder_unavailable() -> None:
    def broken(**kwargs):
        raise OSError("model files missing and local_files_only is set")

    failing = embedder(broken)
    with pytest.raises(EmbedderUnavailable):
        failing.embed(["x"], "query")
    with pytest.raises(EmbedderUnavailable):
        failing.warm()


def test_empty_offline_cache_raises_embedder_unavailable(tmp_path) -> None:
    settings = EmbeddingSettings(cache_dir=tmp_path, local_files_only=True)
    router = build_router(settings)
    assert router.route("Latn") is None
    with pytest.raises(EmbedderUnavailable):
        router.embed(["knee"], "query")


def test_router_falls_back_along_the_chain() -> None:
    hash_embedder = HashEmbedder()
    broken = embedder(lambda **kwargs: (_ for _ in ()).throw(OSError("no model")))
    router = EmbedderRouter(
        {"minilm": broken, "potion": hash_embedder}, {"Latn": ["minilm", "potion"]}, "potion"
    )
    route = router.route("Latn")
    assert route is not None and route.model_key == "potion"
    assert router.route("Taml") is None and not router.has_route("Taml")
    assert router.model_ids() == {"Latn": broken.model_id}
    with pytest.raises(ValueError):
        EmbedderRouter({"a": hash_embedder}, {"Latn": ["missing"]}, "a")


def test_null_embedder_is_never_available() -> None:
    router = EmbedderRouter({"null": NullEmbedder()}, {"Latn": ["null"]}, "null")
    assert router.route("Latn") is None


async def test_cache_only_embeds_misses() -> None:
    model = HashEmbedder()
    cache = EmbeddingCache(None, memory_size=10)
    first = await cache.embed(model, ["knee", "hip", "knee"], "query")
    second = await cache.embed(model, ["hip", "knee"], "query")
    assert model.calls == 1
    assert np.allclose(first[0], second[1])
    assert cache.hits == 3 and cache.misses == 2


def test_topic_engine_never_reads_youtube_categories() -> None:
    # the topic engine matches only the client's own terms; youtube content categories are off limits
    forbidden = re.compile(
        r"category_?id|categoryId|topicDetails|topic_details|topicCategories", re.IGNORECASE
    )
    offenders = [
        str(path.relative_to(TOPICS_ROOT))
        for path in TOPICS_ROOT.rglob("*")
        if path.suffix in {".py", ".yaml", ".md"} and forbidden.search(path.read_text("utf-8"))
    ]
    assert offenders == []
