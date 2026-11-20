from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from app.core.config import CompetitorSettings
from app.domain.competitors import DriftEvent, YouTubeRelevance
from app.domain.youtube import OwnershipSignal
from app.services.competitors.registries import drift_rule_registry
from app.services.competitors.score.topics import LexicalTopicMatcher

SITE_SIDE = frozenset({"site_links_channel", "jsonld_sameas", "embed_majority"})


@dataclass(frozen=True)
class ChannelSnapshot:
    # the latest cached channel row from 02's daily refresh; reading it costs no quota
    title: str
    handle: str | None
    description: str
    availability: str
    last_upload_at: datetime | None
    recent_texts: tuple[str, ...] = ()


@dataclass(frozen=True)
class TrackedChannel:
    candidate_id: UUID
    channel_id: str
    in_set: bool
    title: str
    handle: str | None
    official_domain: str | None
    signals: tuple[OwnershipSignal, ...]
    relevance: YouTubeRelevance | None
    flags: frozenset[str]
    niche_queries_recent: int = 0


@dataclass
class DriftContext:
    client_id: UUID
    now: datetime
    settings: CompetitorSettings
    matcher: LexicalTopicMatcher
    latest: dict[str, ChannelSnapshot] = field(default_factory=dict)


class DriftRule(Protocol):
    kind: str

    def check(self, tracked: TrackedChannel, ctx: DriftContext) -> DriftEvent | None: ...


def _event(tracked: TrackedChannel, ctx: DriftContext, kind: str, **detail) -> DriftEvent:
    return DriftEvent(
        client_id=ctx.client_id,
        candidate_id=tracked.candidate_id,
        channel_id=tracked.channel_id,
        kind=kind,
        detail=detail,
        detected_at=ctx.now,
    )


def week_marker(now: datetime) -> str:
    year, week, _ = now.isocalendar()
    return f"overlap_low:{year}-W{week:02d}"


def content_overlap(texts: tuple[str, ...], matcher: LexicalTopicMatcher) -> float | None:
    if not texts or not matcher.size:
        return None
    matched = [matcher.match(text) for text in texts]
    share = sum(bool(refs) for refs in matched) / len(texts)
    coverage = min(len(set().union(*matched)) / min(20, matcher.size), 1.0)
    return 0.5 * share + 0.5 * coverage


@drift_rule_registry.register("channel_missing")
class ChannelMissingRule:
    kind = "channel_missing"

    def check(self, tracked: TrackedChannel, ctx: DriftContext) -> DriftEvent | None:
        latest = ctx.latest.get(tracked.channel_id)
        if not tracked.in_set or latest is None or latest.availability != "unavailable":
            return None
        return _event(tracked, ctx, self.kind, title=tracked.title, propose_replacement=True)


@drift_rule_registry.register("inactive")
class InactiveRule:
    kind = "inactive"

    def check(self, tracked: TrackedChannel, ctx: DriftContext) -> DriftEvent | None:
        latest = ctx.latest.get(tracked.channel_id)
        if (
            not tracked.in_set
            or latest is None
            or latest.availability == "unavailable"
            or "inactive" in tracked.flags
        ):
            return None
        cutoff = ctx.now - timedelta(days=ctx.settings.inactive_after_days)
        if latest.last_upload_at is not None and latest.last_upload_at >= cutoff:
            return None
        last = latest.last_upload_at.isoformat() if latest.last_upload_at else None
        return _event(tracked, ctx, self.kind, last_upload_at=last, counts_toward_minimum=False)


@drift_rule_registry.register("renamed")
class RenamedRule:
    kind = "renamed"

    def check(self, tracked: TrackedChannel, ctx: DriftContext) -> DriftEvent | None:
        latest = ctx.latest.get(tracked.channel_id)
        if not tracked.in_set or latest is None or latest.availability == "unavailable":
            return None
        old_handle = (tracked.handle or "").casefold()
        new_handle = (latest.handle or "").casefold()
        if latest.title == tracked.title and old_handle == new_handle:
            return None
        return _event(
            tracked,
            ctx,
            self.kind,
            old_title=tracked.title,
            new_title=latest.title,
            old_handle=tracked.handle,
            new_handle=latest.handle,
        )


@drift_rule_registry.register("ownership_weakened")
class OwnershipWeakenedRule:
    # the channel was owned through its own description link only; if that link disappears, ownership is unproven
    kind = "ownership_weakened"

    def check(self, tracked: TrackedChannel, ctx: DriftContext) -> DriftEvent | None:
        latest = ctx.latest.get(tracked.channel_id)
        if (
            not tracked.in_set
            or latest is None
            or latest.availability == "unavailable"
            or not tracked.official_domain
        ):
            return None
        names = {signal.name for signal in tracked.signals}
        if "channel_links_site" not in names or names & SITE_SIDE:
            return None
        if tracked.official_domain.casefold() in latest.description.casefold():
            return None
        return _event(
            tracked, ctx, self.kind, lost_signal="channel_links_site", domain=tracked.official_domain
        )


@drift_rule_registry.register("overlap_drop")
class OverlapDropRule:
    kind = "overlap_drop"

    def check(self, tracked: TrackedChannel, ctx: DriftContext) -> DriftEvent | None:
        if not self.is_low(tracked, ctx):
            return None
        previous = week_marker(ctx.now - timedelta(days=7))
        if previous not in tracked.flags:
            return None
        return _event(
            tracked, ctx, self.kind, suggest="niche_set", at_confirmation=tracked.relevance.content_overlap
        )

    @staticmethod
    def is_low(tracked: TrackedChannel, ctx: DriftContext) -> bool:
        latest = ctx.latest.get(tracked.channel_id)
        if (
            not tracked.in_set
            or latest is None
            or tracked.relevance is None
            or not tracked.relevance.content_overlap
        ):
            return False
        current = content_overlap(latest.recent_texts, ctx.matcher)
        return (
            current is not None
            and current < ctx.settings.overlap_drop_ratio * tracked.relevance.content_overlap
        )


@drift_rule_registry.register("niche_riser")
class NicheRiserRule:
    kind = "niche_riser"

    def check(self, tracked: TrackedChannel, ctx: DriftContext) -> DriftEvent | None:
        if tracked.in_set or tracked.niche_queries_recent < ctx.settings.niche_riser_min_queries:
            return None
        return _event(
            tracked,
            ctx,
            self.kind,
            distinct_queries=tracked.niche_queries_recent,
            suggest="content competitor",
        )
