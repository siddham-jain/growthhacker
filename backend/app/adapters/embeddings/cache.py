import asyncio
import hashlib
from collections import OrderedDict
from collections.abc import Callable, Sequence
from typing import Literal

import numpy as np
from numpy.typing import NDArray

from app.adapters.embeddings.repository import TextEmbeddingRepository, vector_to_bytes
from app.core.db import UnitOfWork
from app.ports.embeddings import Embedder

Role = Literal["query", "passage"]


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class EmbeddingCache:
    """memory lru over the shared text_embeddings table; only cache misses reach the model."""

    def __init__(self, uow_factory: Callable[[], UnitOfWork] | None, *, memory_size: int = 20_000) -> None:
        self._uow_factory = uow_factory
        self._memory: OrderedDict[tuple[str, str, str], NDArray[np.float32]] = OrderedDict()
        self._memory_size = memory_size
        self.hits = 0
        self.misses = 0

    def _remember(self, key: tuple[str, str, str], vector: NDArray[np.float32]) -> None:
        self._memory[key] = vector
        self._memory.move_to_end(key)
        while len(self._memory) > self._memory_size:
            self._memory.popitem(last=False)

    async def embed(
        self,
        embedder: Embedder,
        texts: Sequence[str],
        role: Role,
        *,
        source_video_ids: Sequence[str | None] | None = None,
    ) -> NDArray[np.float32]:
        if not texts:
            return np.zeros((0, embedder.dim), dtype=np.float32)
        hashes = [text_hash(text) for text in texts]
        vectors: dict[str, NDArray[np.float32]] = {}
        for digest in hashes:
            cached = self._memory.get((embedder.model_id, role, digest))
            if cached is not None:
                vectors[digest] = cached
        missing = [digest for digest in dict.fromkeys(hashes) if digest not in vectors]
        if missing and self._uow_factory is not None:
            async with self._uow_factory() as uow:
                stored = await TextEmbeddingRepository(uow.session).get_many(embedder.model_id, role, missing)
            vectors.update(stored)
        to_compute: dict[str, int] = {}
        for index, digest in enumerate(hashes):
            if digest not in vectors and digest not in to_compute:
                to_compute[digest] = index
        self.hits += len(hashes) - len(to_compute)
        self.misses += len(to_compute)
        if to_compute:
            indexes = list(to_compute.values())
            computed = await asyncio.to_thread(embedder.embed, [texts[i] for i in indexes], role)
            rows = []
            for digest, index, vector in zip(to_compute, indexes, computed, strict=True):
                vectors[digest] = vector
                source = source_video_ids[index] if source_video_ids is not None else None
                rows.append(
                    {
                        "model_id": embedder.model_id,
                        "text_hash": digest,
                        "role": role,
                        "dim": embedder.dim,
                        "vector": vector_to_bytes(vector),
                        "source_video_id": source,
                    }
                )
            if self._uow_factory is not None:
                async with self._uow_factory() as uow:
                    await TextEmbeddingRepository(uow.session).put_many(rows)
                    await uow.commit()
        for digest in hashes:
            self._remember((embedder.model_id, role, digest), vectors[digest])
        return np.vstack([vectors[digest] for digest in hashes]).astype(np.float32)

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0
