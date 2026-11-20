from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

import cv2
import numpy as np
import regex

from app.services.analytics.settings import ThumbnailSettings

ScriptHint = Literal["latin", "devanagari"]
NON_ALNUM = regex.compile(r"[^\p{L}\p{M}\p{N}]+")
DEVANAGARI_CLUSTER = regex.compile(r"[ऀ-ॿ]+")
LATIN_TOKEN = regex.compile(r"[\p{Latin}\p{N}]{2,}")
DEVANAGARI = regex.compile(r"[ऀ-ॿ]")
GRID = 3


@dataclass(frozen=True, slots=True)
class TextBox:
    points: tuple[tuple[float, float], ...]
    text: str
    confidence: float

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        xs, ys = [p[0] for p in self.points], [p[1] for p in self.points]
        return min(xs), min(ys), max(xs), max(ys)

    @property
    def height(self) -> float:
        _, top, _, bottom = self.bounds
        return bottom - top

    @property
    def area(self) -> float:
        left, top, right, bottom = self.bounds
        return max(0.0, right - left) * max(0.0, bottom - top)


class ThumbnailTextReader(Protocol):
    engine: str
    model: str

    def read(self, image: np.ndarray, script: ScriptHint) -> list[TextBox]: ...


def normalised(text: str) -> str:
    return NON_ALNUM.sub("", text.casefold())


def grid_cell(box: TextBox, width: int, height: int) -> int:
    left, top, right, bottom = box.bounds
    column = min(GRID - 1, max(0, int(((left + right) / 2) / max(width, 1) * GRID)))
    row = min(GRID - 1, max(0, int(((top + bottom) / 2) / max(height, 1) * GRID)))
    return row * GRID + column


def _iou(a: TextBox, b: TextBox) -> float:
    al, at, ar, ab = a.bounds
    bl, bt, br, bb = b.bounds
    inter = max(0.0, min(ar, br) - max(al, bl)) * max(0.0, min(ab, bb) - max(at, bt))
    union = a.area + b.area - inter
    return inter / union if union > 0 else 0.0


def merge_passes(first: Sequence[TextBox], second: Sequence[TextBox]) -> list[TextBox]:
    # both script passes are kept per box and the higher-confidence reading wins
    merged = list(first)
    for candidate in second:
        overlaps = [i for i, box in enumerate(merged) if _iou(box, candidate) >= 0.5]
        if not overlaps:
            merged.append(candidate)
            continue
        index = overlaps[0]
        if candidate.confidence > merged[index].confidence:
            merged[index] = candidate
    return merged


def count_words(text: str) -> int:
    return len(LATIN_TOKEN.findall(text)) + len(DEVANAGARI_CLUSTER.findall(text))


def script_of_boxes(boxes: Iterable[TextBox]) -> Literal["latin", "devanagari", "mixed", "none"]:
    letters = [ch for box in boxes for ch in box.text if ch.isalpha() or DEVANAGARI.match(ch)]
    if not letters:
        return "none"
    deva = sum(1 for ch in letters if DEVANAGARI.match(ch)) / len(letters)
    if deva >= 0.8:
        return "devanagari"
    return "mixed" if deva >= 0.2 else "latin"


def word_count_band(count: int | None) -> str | None:
    if count is None:
        return None
    if count == 0:
        return "0"
    if count <= 3:
        return "1-3"
    return "4-6" if count <= 6 else "7+"


@dataclass(frozen=True, slots=True)
class OcrPage:
    boxes: tuple[TextBox, ...]
    width: int
    height: int

    def text_keys(self) -> frozenset[tuple[str, int]]:
        return frozenset(
            (normalised(b.text), grid_cell(b, self.width, self.height))
            for b in self.boxes
            if normalised(b.text)
        )


@dataclass(frozen=True, slots=True)
class TextSummary:
    text_present: bool
    word_count: int | None
    word_count_band: str | None
    text_box_count: int
    text_area_ratio: float
    largest_text_height_ratio: float
    brand_text_only: bool
    script_detected: Literal["latin", "devanagari", "mixed", "none", "unknown"]
    confidence_mean: float | None


class ThumbnailTextAnalyzer:
    def __init__(self, reader: ThumbnailTextReader, settings: ThumbnailSettings) -> None:
        self.reader = reader
        self.settings = settings

    def read(self, rgb: np.ndarray, devanagari_hint: bool) -> OcrPage:
        image = self._upscaled(rgb)
        height, width = image.shape[:2]
        first = self._geometry_filtered(self.reader.read(image, "latin"), height)
        mean_conf = float(np.mean([b.confidence for b in first])) if first else None
        if devanagari_hint or (mean_conf is not None and mean_conf < self.settings.ocr_unreadable_conf):
            second = self._geometry_filtered(self.reader.read(image, "devanagari"), height)
            first = merge_passes(first, second)
        ordered = sorted(first, key=lambda b: (b.bounds[1], b.bounds[0], b.text))
        return OcrPage(boxes=tuple(ordered), width=width, height=height)

    def _upscaled(self, rgb: np.ndarray) -> np.ndarray:
        height, width = rgb.shape[:2]
        if height >= self.settings.ocr_min_height:
            return rgb
        scale = self.settings.ocr_min_height / height
        return cv2.resize(
            rgb, (round(width * scale), self.settings.ocr_min_height), interpolation=cv2.INTER_CUBIC
        )

    def _geometry_filtered(self, boxes: Sequence[TextBox], height: int) -> list[TextBox]:
        # tiny boxes are watermarks and timestamps; detection alone counts as text even if unreadable
        return [b for b in boxes if b.height >= self.settings.ocr_min_box_height_ratio * height]

    def summarise(
        self, page: OcrPage, recurring: frozenset[tuple[str, int]], brand_tokens: frozenset[str]
    ) -> TextSummary:
        boxes = [
            b
            for b in page.boxes
            if (normalised(b.text), grid_cell(b, page.width, page.height)) not in recurring
        ]
        if not boxes:
            return TextSummary(False, 0, "0", 0, 0.0, 0.0, False, "none", None)
        image_area = float(page.width * page.height)
        readable = [b for b in boxes if b.confidence >= self.settings.ocr_min_conf]
        mean_conf = float(np.mean([b.confidence for b in boxes]))
        brand = [b for b in readable if self._is_brand(b, brand_tokens)]
        counted = [b for b in readable if b not in brand]
        unreadable = mean_conf < self.settings.ocr_unreadable_conf
        words = None if unreadable else sum(count_words(b.text) for b in counted)
        return TextSummary(
            text_present=True,
            word_count=words,
            word_count_band=word_count_band(words),
            text_box_count=len(boxes),
            text_area_ratio=min(1.0, sum(b.area for b in boxes) / image_area),
            largest_text_height_ratio=min(1.0, max(b.height for b in boxes) / page.height),
            brand_text_only=bool(readable) and len(brand) == len(readable),
            script_detected="unknown" if unreadable else script_of_boxes(readable),
            confidence_mean=mean_conf,
        )

    @staticmethod
    def _is_brand(box: TextBox, brand_tokens: frozenset[str]) -> bool:
        tokens = [normalised(token) for token in box.text.split()]
        tokens = [t for t in tokens if t]
        return bool(tokens) and all(t in brand_tokens for t in tokens)


def brand_tokens_of(channel_title: str) -> frozenset[str]:
    return frozenset(t for t in (normalised(part) for part in channel_title.split()) if len(t) >= 3)


def recurring_text(
    pages: Sequence[OcrPage], min_share: float, min_pages: int
) -> dict[tuple[str, int], float]:
    # string frequency per grid cell across a channel's recent thumbnails; not logo recognition
    if len(pages) < min_pages:
        return {}
    counts = Counter(key for page in pages for key in page.text_keys())
    return {key: n / len(pages) for key, n in sorted(counts.items()) if n / len(pages) >= min_share}
