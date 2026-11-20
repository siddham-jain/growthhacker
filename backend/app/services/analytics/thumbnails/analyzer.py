from dataclasses import dataclass
from datetime import datetime

from app.domain.analytics import ThumbnailTraits
from app.services.analytics.settings import ThumbnailSettings
from app.services.analytics.thumbnails.fetch import FetchedThumbnail
from app.services.analytics.thumbnails.pixels import PixelStats, decode_rgb, pixel_stats
from app.services.analytics.thumbnails.text import (
    OcrPage,
    ThumbnailTextAnalyzer,
    ThumbnailTextReader,
)

TRAITS_VERSION = "thumb-2026.11.1"


@dataclass(frozen=True, slots=True)
class ReadThumbnail:
    fetched: FetchedThumbnail
    width: int
    height: int
    page: OcrPage
    pixels: PixelStats


class ThumbnailTraitBuilder:
    """simple visual traits only: text presence and amount, brightness, contrast, colourfulness. no faces."""

    def __init__(self, reader: ThumbnailTextReader, settings: ThumbnailSettings) -> None:
        self.reader = reader
        self.settings = settings
        self.text = ThumbnailTextAnalyzer(reader, settings)

    def read(self, fetched: FetchedThumbnail, devanagari_hint: bool) -> ReadThumbnail | None:
        rgb = decode_rgb(fetched.data)
        if rgb is None:
            return None
        stats = pixel_stats(rgb, self.settings)
        page = self.text.read(stats.cropped, devanagari_hint)
        return ReadThumbnail(
            fetched=fetched, width=rgb.shape[1], height=rgb.shape[0], page=page, pixels=stats
        )

    def traits(
        self,
        video_id: str,
        read: ReadThumbnail,
        recurring: frozenset[tuple[str, int]],
        brand_tokens: frozenset[str],
        computed_at: datetime,
    ) -> ThumbnailTraits:
        stats = read.pixels
        summary = self.text.summarise(read.page, recurring, brand_tokens)
        return ThumbnailTraits(
            video_id=video_id,
            source_key=read.fetched.source_key,
            source_w=read.width,
            source_h=read.height,
            content_sha256=read.fetched.sha256,
            letterbox_cropped=stats.letterbox_cropped,
            pillarbox_detected=stats.pillarbox_detected,
            text_present=summary.text_present,
            word_count=summary.word_count,
            word_count_band=summary.word_count_band,
            text_box_count=summary.text_box_count,
            text_area_ratio=summary.text_area_ratio,
            largest_text_height_ratio=summary.largest_text_height_ratio,
            brand_text_only=summary.brand_text_only,
            script_detected=summary.script_detected,
            ocr_engine=self.reader.engine,
            ocr_model=self.reader.model,
            ocr_confidence_mean=summary.confidence_mean,
            brightness=stats.brightness,
            brightness_band=stats.brightness_band,
            rms_contrast=stats.rms_contrast,
            contrast_band=stats.contrast_band,
            colorfulness=stats.colorfulness,
            colorfulness_band=stats.colorfulness_band,
            dominant_colour_count=stats.dominant_colour_count,
            traits_version=TRAITS_VERSION,
            computed_at=computed_at,
        )
