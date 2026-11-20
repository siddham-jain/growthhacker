from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

import numpy as np
from numpy.typing import NDArray

from app.ports.embeddings import Embedder, EmbedderUnavailable


@dataclass(frozen=True)
class Route:
    model_key: str
    embedder: Embedder


class EmbedderRouter:
    """picks a model per script with a fallback chain; it is itself an Embedder for the default model."""

    def __init__(
        self, embedders: Mapping[str, Embedder], routes: Mapping[str, Sequence[str]], default_key: str
    ) -> None:
        unknown = {key for chain in routes.values() for key in chain if key not in embedders}
        if default_key not in embedders or unknown:
            raise ValueError(f"routes reference unknown embedding models: {sorted(unknown | {default_key})}")
        self._embedders = dict(embedders)
        self._routes = {script: list(chain) for script, chain in routes.items()}
        self._default_key = default_key
        self._available: dict[str, bool] = {}

    @property
    def model_id(self) -> str:
        return self._embedders[self._default_key].model_id

    @property
    def dim(self) -> int:
        return self._embedders[self._default_key].dim

    def embed(self, texts: Sequence[str], role: Literal["query", "passage"]) -> NDArray[np.float32]:
        return self._embedders[self._default_key].embed(texts, role)

    def is_available(self, model_key: str) -> bool:
        if model_key not in self._available:
            embedder = self._embedders[model_key]
            try:
                warm = getattr(embedder, "warm", None)
                if warm is not None:
                    warm()
                elif embedder.dim == 0:
                    raise EmbedderUnavailable(f"{model_key} has no vectors")
                self._available[model_key] = True
            except EmbedderUnavailable:
                self._available[model_key] = False
        return self._available[model_key]

    def mark_unavailable(self, model_key: str) -> None:
        self._available[model_key] = False

    def route(self, script: str) -> Route | None:
        for model_key in self._routes.get(script, []):
            if self.is_available(model_key):
                return Route(model_key=model_key, embedder=self._embedders[model_key])
        return None

    def has_route(self, script: str) -> bool:
        return bool(self._routes.get(script))

    def model_ids(self) -> dict[str, str]:
        return {script: self._embedders[chain[0]].model_id for script, chain in self._routes.items() if chain}
