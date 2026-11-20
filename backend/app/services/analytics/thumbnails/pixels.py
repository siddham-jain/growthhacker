from dataclasses import dataclass

import cv2
import numpy as np

from app.services.analytics.settings import ThumbnailSettings

LUMA_WEIGHTS = np.array([0.299, 0.587, 0.114])
HUE_BINS = 12


def decode_rgb(data: bytes) -> np.ndarray | None:
    buffer = np.frombuffer(data, dtype=np.uint8)
    image = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    if image is None or image.size == 0:
        return None
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)


def luma(rgb: np.ndarray) -> np.ndarray:
    return (rgb.astype(np.float64) @ LUMA_WEIGHTS) / 255.0


def _dark_run(means: np.ndarray, stds: np.ndarray, settings: ThumbnailSettings, limit: int) -> int:
    run = 0
    while run < limit and means[run] < settings.letterbox_luma and stds[run] < settings.letterbox_std:
        run += 1
    return run


def crop_bars(rgb: np.ndarray, settings: ThumbnailSettings, axis: int) -> tuple[np.ndarray, bool]:
    # axis 0 crops rows (letterbox), axis 1 crops columns
    lum = luma(rgb)
    lines = lum if axis == 0 else lum.T
    means, stds = lines.mean(axis=1), lines.std(axis=1)
    limit = int(len(lines) * settings.letterbox_max_share)
    head = _dark_run(means, stds, settings, limit)
    tail = _dark_run(means[::-1], stds[::-1], settings, limit)
    if head == 0 and tail == 0:
        return rgb, False
    end = len(lines) - tail
    cropped = rgb[head:end] if axis == 0 else rgb[:, head:end]
    return cropped, True


def _leading_true(mask: np.ndarray) -> int:
    return len(mask) if mask.all() else int(np.argmin(mask))


def detect_pillarbox(rgb: np.ndarray, settings: ThumbnailSettings) -> bool:
    # uniform side bands of at least 18% each suggest a vertical source framed in a wide thumbnail
    uniform = luma(rgb).std(axis=0) < settings.pillarbox_std
    if uniform.all():
        return False
    minimum = settings.pillarbox_min_share * len(uniform)
    return _leading_true(uniform) >= minimum and _leading_true(uniform[::-1]) >= minimum


def downsample(rgb: np.ndarray, max_width: int) -> np.ndarray:
    height, width = rgb.shape[:2]
    if width <= max_width:
        return rgb
    scale = max_width / width
    return cv2.resize(rgb, (max_width, max(1, round(height * scale))), interpolation=cv2.INTER_AREA)


def colorfulness(rgb: np.ndarray) -> float:
    # hasler and suesstrunk (2003) on 0..255 rgb
    channels = rgb.astype(np.float64)
    red, green, blue = channels[..., 0], channels[..., 1], channels[..., 2]
    rg = red - green
    yb = 0.5 * (red + green) - blue
    sigma = np.sqrt(rg.std() ** 2 + yb.std() ** 2)
    mu = np.sqrt(rg.mean() ** 2 + yb.mean() ** 2)
    return float(sigma + 0.3 * mu)


def dominant_colour_count(rgb: np.ndarray, min_share: float) -> int:
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV).reshape(-1, 3).astype(np.float64)
    hue = hsv[:, 0] * 2.0
    saturation, value = hsv[:, 1] / 255.0, hsv[:, 2] / 255.0
    chromatic = (saturation >= 0.2) & (value >= 0.2)
    bins = np.where(chromatic, np.minimum((hue // 30).astype(int), HUE_BINS - 1), -1)
    black = ~chromatic & (value < 0.2)
    white = ~chromatic & (value > 0.85) & (saturation < 0.2)
    bins = np.where(black, HUE_BINS, bins)
    bins = np.where(white, HUE_BINS + 1, bins)
    bins = np.where(bins == -1, HUE_BINS + 2, bins)
    counts = np.bincount(bins, minlength=HUE_BINS + 3)
    return int((counts / counts.sum() >= min_share).sum())


def band3(
    value: float, edges: tuple[float, float], labels: tuple[str, str, str], upper_inclusive: bool
) -> str:
    low, high = edges
    if value < low:
        return labels[0]
    if value < high or (upper_inclusive and value == high):
        return labels[1]
    return labels[2]


@dataclass(frozen=True, slots=True)
class PixelStats:
    cropped: np.ndarray
    letterbox_cropped: bool
    pillarbox_detected: bool
    brightness: float
    rms_contrast: float
    colorfulness: float
    dominant_colour_count: int
    brightness_band: str
    contrast_band: str
    colorfulness_band: str


def pixel_stats(rgb: np.ndarray, settings: ThumbnailSettings) -> PixelStats:
    rows_cropped, letterboxed = crop_bars(rgb, settings, axis=0)
    pillarbox = detect_pillarbox(rows_cropped, settings)
    cropped, columns_cropped = crop_bars(rows_cropped, settings, axis=1)
    small = downsample(cropped, settings.stats_max_width)
    lum = luma(small)
    brightness, contrast, colour = float(lum.mean()), float(lum.std()), colorfulness(small)
    return PixelStats(
        cropped=cropped,
        letterbox_cropped=letterboxed or columns_cropped,
        pillarbox_detected=pillarbox,
        brightness=min(max(brightness, 0.0), 1.0),
        rms_contrast=contrast,
        colorfulness=colour,
        dominant_colour_count=dominant_colour_count(small, settings.dominant_colour_min_share),
        brightness_band=band3(brightness, settings.brightness_bands, ("dark", "mid", "bright"), True),
        contrast_band=band3(contrast, settings.contrast_bands, ("low", "mid", "high"), True),
        colorfulness_band=band3(colour, settings.colorfulness_bands, ("low", "medium", "high"), False),
    )
