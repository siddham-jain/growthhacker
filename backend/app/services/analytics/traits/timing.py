from functools import cache
from importlib import resources
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.registry import trait_extractor_registry
from app.domain.analytics import TimingTraits
from app.domain.youtube import VideoRecord
from app.services.analytics.traits.base import Features, TraitContext, flag

UTC_LABEL = "UTC"
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


@cache
def _zones_by_country() -> dict[str, tuple[str, ...]]:
    text = resources.files("tzdata").joinpath("zoneinfo", "zone.tab").read_text(encoding="utf-8")
    zones: dict[str, list[str]] = {}
    for line in text.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        code, _, zone, *_ = line.split("\t")
        zones.setdefault(code, []).append(zone)
    return {code: tuple(sorted(items)) for code, items in zones.items()}


def resolve_timezone(timezone: str | None, country: str | None) -> str:
    # an explicit zone wins; a single-zone country is the fallback; otherwise utc, labelled as such
    if timezone:
        try:
            ZoneInfo(timezone)
            return timezone
        except (ZoneInfoNotFoundError, ValueError):
            pass
    zones = _zones_by_country().get((country or "").upper(), ())
    return zones[0] if len(zones) == 1 else UTC_LABEL


def daypart(hour: int) -> str:
    if hour <= 5:
        return "night"
    if hour <= 11:
        return "morning"
    if hour <= 16:
        return "afternoon"
    return "evening" if hour <= 21 else "late"


@trait_extractor_registry.register("timing")
class TimingTraitExtractor:
    key = "timing"
    scope = "client_context"
    version = "timing-1"

    def extract(self, video: VideoRecord, ctx: TraitContext) -> TimingTraits:
        zone = resolve_timezone(ctx.client.timezone, ctx.client.country)
        local = video.published_at.astimezone(ZoneInfo(zone))
        weekday = local.weekday()
        return TimingTraits(
            client_timezone=zone,
            publish_weekday=weekday,
            publish_hour_local=local.hour,
            daypart=daypart(local.hour),
            is_weekend=weekday in ctx.settings.traits.weekend_days,
        )

    def mined(self, part: TimingTraits) -> Features:
        return {
            "timing.weekday": frozenset({WEEKDAYS[part.publish_weekday]}),
            "timing.daypart": frozenset({part.daypart}),
            "timing.is_weekend": flag(part.is_weekend),
        }
