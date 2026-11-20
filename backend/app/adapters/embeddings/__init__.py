from typing import Any

from app.adapters.embeddings.fastembed_embedder import FastEmbedEmbedder
from app.adapters.embeddings.null_embedder import NullEmbedder
from app.adapters.embeddings.router import EmbedderRouter, Route
from app.core.config import EmbeddingSettings
from app.core.registry import adapter_registry


def build_router(settings: EmbeddingSettings) -> EmbedderRouter:
    embedders = {
        key: FastEmbedEmbedder(
            model,
            cache_dir=settings.cache_dir,
            threads=settings.threads,
            batch_size=settings.batch_size,
            local_files_only=settings.local_files_only,
        )
        for key, model in settings.models.items()
    }
    return EmbedderRouter(embedders, settings.routes, settings.default_model)


@adapter_registry.register("embedder", "fastembed")
def fastembed_router(container: Any) -> EmbedderRouter:
    return build_router(container.settings.embeddings)


@adapter_registry.register("embedder", "null")
def null_router(container: Any) -> EmbedderRouter:
    return EmbedderRouter({"null": NullEmbedder()}, {}, "null")


__all__ = ["EmbedderRouter", "FastEmbedEmbedder", "NullEmbedder", "Route", "build_router"]
