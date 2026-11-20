from collections.abc import Sequence
from typing import Literal, Protocol

import numpy as np
from numpy.typing import NDArray


class EmbedderUnavailable(RuntimeError):
    pass


class Embedder(Protocol):
    model_id: str
    dim: int

    # returns l2-normalised float32 rows, one per input text
    def embed(self, texts: Sequence[str], role: Literal["query", "passage"]) -> NDArray[np.float32]: ...
