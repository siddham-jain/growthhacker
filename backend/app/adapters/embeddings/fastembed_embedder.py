import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

import numpy as np
from numpy.typing import NDArray

from app.core.config import EmbeddingModelSettings
from app.ports.embeddings import EmbedderUnavailable


def l2_normalise(vectors: NDArray[np.floating[Any]]) -> NDArray[np.float32]:
    matrix = np.asarray(vectors, dtype=np.float32)
    if matrix.ndim == 1:
        matrix = matrix[None, :]
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (matrix / norms).astype(np.float32)


class FastEmbedEmbedder:
    """onnx cpu embeddings; the model loads lazily once and output is always l2-normalised."""

    def __init__(
        self,
        model: EmbeddingModelSettings,
        *,
        cache_dir: Path,
        threads: int | None,
        batch_size: int,
        local_files_only: bool,
        model_factory: Any = None,
    ) -> None:
        self.model_id = f"{model.name}@{model.revision}"
        self.dim = model.dim
        self._settings = model
        self._cache_dir = cache_dir
        self._threads = threads
        self._batch_size = batch_size
        self._local_files_only = local_files_only
        self._model_factory = model_factory
        self._model: Any = None
        self._load_error: Exception | None = None
        self._lock = threading.Lock()

    def _load(self) -> Any:
        if self._model is not None:
            return self._model
        with self._lock:
            if self._load_error is not None:
                raise EmbedderUnavailable(f"{self.model_id}: {self._load_error}") from self._load_error
            if self._model is None:
                try:
                    factory = self._model_factory or _text_embedding_factory()
                    self._model = factory(
                        model_name=self._settings.name,
                        cache_dir=str(self._cache_dir),
                        threads=self._threads,
                        local_files_only=self._local_files_only,
                    )
                except Exception as error:
                    self._load_error = error
                    raise EmbedderUnavailable(f"{self.model_id}: {error}") from error
        return self._model

    def warm(self) -> None:
        self._load()

    def embed(self, texts: Sequence[str], role: Literal["query", "passage"]) -> NDArray[np.float32]:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        model = self._load()
        prefix = self._settings.query_prefix if role == "query" else self._settings.passage_prefix
        inputs = [f"{prefix}{text}" for text in texts]
        try:
            rows = list(model.embed(inputs, batch_size=self._batch_size))
        except Exception as error:
            raise EmbedderUnavailable(f"{self.model_id}: inference failed: {error}") from error
        vectors = l2_normalise(np.vstack(rows))
        if vectors.shape != (len(texts), self.dim):
            raise EmbedderUnavailable(f"{self.model_id}: expected dim {self.dim}, got {vectors.shape[1]}")
        return vectors


def _text_embedding_factory() -> Any:
    from fastembed import TextEmbedding

    return TextEmbedding
