from collections.abc import Sequence
from typing import Literal

import numpy as np
from numpy.typing import NDArray

from app.ports.embeddings import EmbedderUnavailable


class NullEmbedder:
    """stands in when no model can be loaded; callers degrade to lexical + llm matching."""

    model_id = "null@0"
    dim = 0

    def embed(self, texts: Sequence[str], role: Literal["query", "passage"]) -> NDArray[np.float32]:
        raise EmbedderUnavailable("no embedding model is configured")
