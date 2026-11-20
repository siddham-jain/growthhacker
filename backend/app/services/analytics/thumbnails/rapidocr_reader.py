import os
import threading
from typing import Any

import numpy as np

from app.core.registry import adapter_registry
from app.services.analytics.thumbnails.text import ScriptHint, TextBox


def devanagari_params() -> dict[str, Any]:
    # rapidocr 3.10+ rejects plain strings for these keys; it wants its own enums
    from rapidocr import LangRec, ModelType, OCRVersion

    return {
        "Rec.lang_type": LangRec.DEVANAGARI,
        "Rec.ocr_version": OCRVersion.PPOCRV5,
        "Rec.model_type": ModelType.MOBILE,
    }


class RapidOcrReader:
    """rapidocr 3.x (paddleocr db detector + recogniser on onnxruntime); local, no network at read time."""

    engine = "rapidocr"

    def __init__(self, workers: int = 1) -> None:
        from rapidocr import RapidOCR

        self._factory = RapidOCR
        # one engine per concurrent read, sharing the cores so parallel sessions do not oversubscribe
        self._threads_per_engine = max(1, (os.cpu_count() or 1) // workers)
        self._idle: dict[str, list[Any]] = {"latin": [], "devanagari": []}
        self._lock = threading.Lock()
        self.model = "PP-OCRv6-small"

    def _checkout(self, script: ScriptHint) -> Any:
        with self._lock:
            if self._idle[script]:
                return self._idle[script].pop()
        params: dict[str, Any] = {
            "Global.log_level": "error",
            "Global.text_score": 0.0,
            "EngineConfig.onnxruntime.intra_op_num_threads": self._threads_per_engine,
        }
        if script == "devanagari":
            params |= devanagari_params()
        return self._factory(params=params)

    def read(self, image: np.ndarray, script: ScriptHint) -> list[TextBox]:
        engine = self._checkout(script)
        try:
            output = engine(np.ascontiguousarray(image[..., ::-1]))
        finally:
            with self._lock:
                self._idle[script].append(engine)
        if getattr(output, "boxes", None) is None or getattr(output, "txts", None) is None:
            return []
        return [
            TextBox(
                points=tuple((float(x), float(y)) for x, y in box),
                text=str(text),
                confidence=float(score),
            )
            for box, text, score in zip(output.boxes, output.txts, output.scores, strict=True)
        ]


@adapter_registry.register("thumbnail_text_reader", "rapidocr")
def build_rapidocr_reader(container: Any) -> RapidOcrReader:
    return RapidOcrReader(container.settings.analytics.thumbnail_ocr_workers)
