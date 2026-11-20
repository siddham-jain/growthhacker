from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
from numpy.typing import NDArray
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.models.topics import TextEmbedding
from app.repositories.shared.base import SharedRepository, chunked


def vector_to_bytes(vector: NDArray[np.float32]) -> bytes:
    return np.asarray(vector, dtype=np.float32).tobytes()


def bytes_to_vector(raw: bytes, dim: int) -> NDArray[np.float32]:
    vector = np.frombuffer(raw, dtype=np.float32)
    if vector.shape != (dim,):
        raise ValueError(f"stored vector has {vector.shape[0]} values, expected {dim}")
    return vector


class TextEmbeddingRepository(SharedRepository):
    """global cache keyed by (model_id, text_hash, role); rows cascade with their source video."""

    async def get_many(
        self, model_id: str, role: str, text_hashes: Sequence[str]
    ) -> dict[str, NDArray[np.float32]]:
        if not text_hashes:
            return {}
        found: dict[str, NDArray[np.float32]] = {}
        for start in range(0, len(text_hashes), 1000):
            batch = list(text_hashes[start : start + 1000])
            statement = select(TextEmbedding.text_hash, TextEmbedding.dim, TextEmbedding.vector).where(
                TextEmbedding.model_id == model_id,
                TextEmbedding.role == role,
                TextEmbedding.text_hash.in_(batch),
            )
            for text_hash, dim, raw in (await self.session.execute(statement)).all():
                found[text_hash] = bytes_to_vector(raw, dim)
        return found

    async def put_many(self, rows: Sequence[Mapping[str, Any]]) -> int:
        table = TextEmbedding.__table__
        written = 0
        for chunk in chunked(list(rows), 6):
            statement = insert(table).values(list(chunk)).on_conflict_do_nothing()
            written += (await self.session.execute(statement)).rowcount
        return written
