import numpy as np
import pytest

from app.adapters.embeddings import build_router
from app.adapters.embeddings.cache import EmbeddingCache
from app.core.config import EmbeddingSettings

pytestmark = pytest.mark.live


async def test_multilingual_minilm_embeds_hindi_hinglish_and_english() -> None:
    # downloads the model into the configured cache once; later runs are offline
    router = build_router(EmbeddingSettings())
    route = router.route("Deva")
    assert route is not None and route.model_key == "minilm"
    texts = ["knee replacement cost", "घुटने का प्रत्यारोपण", "ghutne ka operation", "pizza recipe at home"]
    vectors = await EmbeddingCache(None).embed(route.embedder, texts, "query")
    assert vectors.shape == (4, 384)
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=1e-5)
    similarity = vectors @ vectors.T
    assert similarity[0, 1] > similarity[0, 3] + 0.3
    assert route.embedder.model_id.startswith("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2@")
