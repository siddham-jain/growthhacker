from datetime import timedelta

from app.domain.competitors import DriftEvent
from app.services.competitors.drift.rules import (
    DriftContext,
    DriftRule,
    OverlapDropRule,
    TrackedChannel,
    week_marker,
)
from app.services.competitors.registries import drift_rule_registry

MARKER_PREFIX = "overlap_low:"


class DriftDetector:
    """daily checks over cached channel rows; it never calls the youtube api."""

    def __init__(self) -> None:
        self.rules: list[DriftRule] = [rule() for rule in drift_rule_registry.all().values()]

    def detect(self, channels: list[TrackedChannel], ctx: DriftContext) -> list[DriftEvent]:
        return [
            event
            for tracked in channels
            for rule in self.rules
            if (event := rule.check(tracked, ctx)) is not None
        ]

    @staticmethod
    def overlap_markers(tracked: TrackedChannel, ctx: DriftContext) -> set[str]:
        # weekly low-overlap markers kept on the match; two consecutive low weeks raise overlap_drop
        if not OverlapDropRule.is_low(tracked, ctx):
            return set()
        previous = week_marker(ctx.now - timedelta(days=7))
        return {week_marker(ctx.now), *({previous} & tracked.flags)}
