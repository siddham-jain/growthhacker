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

    def __init__(self) -> None:
        from rapidocr import RapidOCR

        self._factory = RapidOCR
        self._engines: dict[str, Any] = {}
        self._lock = threading.Lock()
        self.model = "PP-OCRv6-small"

    def _engine(self, script: ScriptHint) -> Any:
        with self._lock:
            if script not in self._engines:
                params: dict[str, Any] = {"Global.log_level": "error", "Global.text_score": 0.0}
                if script == "devanagari":
                    params |= devanagari_params()
                self._engines[script] = self._factory(params=params)
            return self._engines[script]

    def read(self, image: np.ndarray, script: ScriptHint) -> list[TextBox]:
        engine = self._engine(script)
        with self._lock:
            output = engine(np.ascontiguousarray(image[..., ::-1]))
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
    return RapidOcrReader()
