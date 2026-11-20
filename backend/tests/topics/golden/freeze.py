"""records real minilm vectors for the golden scenarios: uv run python -m tests.topics.golden.freeze"""

import asyncio
import hashlib
from collections.abc import Sequence
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from app.adapters.embeddings.fastembed_embedder import FastEmbedEmbedder
from app.core.config import EmbeddingSettings
from tests.topics.golden import apollo, runable
from tests.topics.golden.scenario import run_scenario

FROZEN_FILE = Path(__file__).parent / "embeddings.npz"


class RecordingEmbedder:
    def __init__(self, inner: FastEmbedEmbedder) -> None:
        self._inner = inner
        self.model_id = inner.model_id
        self.dim = inner.dim
        self.recorded: dict[str, NDArray[np.float32]] = {}

    def embed(self, texts: Sequence[str], role: str) -> NDArray[np.float32]:
        vectors = self._inner.embed(texts, role)  # type: ignore[arg-type]
        for text, vector in zip(texts, vectors, strict=True):
            self.recorded[hashlib.sha256(text.encode()).hexdigest()] = vector
        return vectors


async def main() -> None:
    settings = EmbeddingSettings()
    model = settings.models[settings.default_model]
    recorder = RecordingEmbedder(
        FastEmbedEmbedder(
            model,
            cache_dir=settings.cache_dir,
            threads=settings.threads,
            batch_size=64,
            local_files_only=False,
        )
    )
    for fixture in (apollo, runable):
        await run_scenario(fixture, recorder)
    keys = sorted(recorder.recorded)
    np.savez_compressed(
        FROZEN_FILE,
        keys=np.array(keys),
        vectors=np.vstack([recorder.recorded[key] for key in keys]).astype(np.float16),
        model_id=np.array(recorder.model_id),
    )
    print(f"froze {len(keys)} vectors from {recorder.model_id} into {FROZEN_FILE.name}")


if __name__ == "__main__":
    asyncio.run(main())
