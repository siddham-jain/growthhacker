from dataclasses import dataclass, field

from app.core.config import CompetitorSettings
from app.services.competitors.resolve.youtube_url import CHANNEL_ID


class SetValidationError(ValueError):
    def __init__(self, code: str, message: str, detail: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail or {}


@dataclass(frozen=True)
class SetSelection:
    competitor_ids: list[str]
    niche_ids: list[str] = field(default_factory=list)
    benchmark_ids: list[str] = field(default_factory=list)
    creator_ids: list[str] = field(default_factory=list)
    own_ids: frozenset[str] = frozenset()
    uncertain_ids: frozenset[str] = frozenset()
    inactive_ids: frozenset[str] = frozenset()
    acknowledge_uncertain: bool = False


class CompetitorSetValidator:
    def __init__(self, settings: CompetitorSettings) -> None:
        self.settings = settings

    def validate(self, selection: SetSelection) -> None:
        settings = self.settings
        competitors = selection.competitor_ids
        count = len(competitors)
        if not settings.min_competitors <= count <= settings.max_competitors:
            raise SetValidationError(
                "competitors_out_of_range",
                f"Pick between {settings.min_competitors} and {settings.max_competitors} competitors ({count} selected).",
                {"selected": count},
            )
        lists = [competitors, selection.niche_ids, selection.benchmark_ids, selection.creator_ids]
        every = [channel_id for channel_ids in lists for channel_id in channel_ids]
        if len(every) != len(set(every)):
            raise SetValidationError("channel_in_two_sets", "A channel can only be in one list.")
        if invalid := [channel_id for channel_id in every if not CHANNEL_ID.match(channel_id)]:
            raise SetValidationError(
                "channel_unresolved", "Every channel must be resolved first.", {"ids": invalid}
            )
        if own := sorted(set(every) & selection.own_ids):
            raise SetValidationError("own_channel", "That looks like your own channel.", {"ids": own})
        uncertain = sorted(set(competitors) & selection.uncertain_ids)
        if uncertain and not selection.acknowledge_uncertain:
            raise SetValidationError(
                "uncertain_ownership",
                "We couldn't confirm some channels are the brand's official channel. Please confirm them.",
                {"ids": uncertain},
            )
        active = [channel_id for channel_id in competitors if channel_id not in selection.inactive_ids]
        if len(active) < settings.min_competitors:
            missing = settings.min_competitors - len(active)
            raise SetValidationError(
                "too_few_active",
                f"Inactive channels don't count toward the minimum. Add at least {missing} more.",
                {"active": len(active)},
            )
