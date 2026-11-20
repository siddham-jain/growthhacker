from datetime import datetime, timedelta
from uuid import UUID

from app.core.config import CompetitorSettings
from app.core.db import SessionFactory
from app.core.tenancy import TenantScope
from app.domain.competitors import CandidateRole, DriftEvent
from app.domain.profile import FreshnessSensitivity, ProfileDiff
from app.domain.topics import TopicRef
from app.services.competitors.inputs import latest_profile
from app.services.competitors.model import DiscoveryResult
from app.services.competitors.registries import candidate_generator_registry
from app.services.competitors.repository import CompetitorRepository
from app.services.competitors.runner import DiscoveryRequest, DiscoveryRunner

FAST_SENSITIVITIES = frozenset({FreshnessSensitivity.high, FreshnessSensitivity.very_high})
PROFILE_FIELDS = ("offerings", "locations")
MEDIUM_OR_HIGH = frozenset({"medium", "high"})


def rediscovery_sources() -> frozenset[str]:
    return frozenset(
        key for key, generator in candidate_generator_registry.all().items() if generator.used_in_rediscovery
    )


class RediscoveryPolicy:
    """monthly by default, every 14 days in fast-moving niches."""

    def __init__(self, settings: CompetitorSettings) -> None:
        self.settings = settings

    def interval_days(self, sensitivity: FreshnessSensitivity | None) -> int:
        if sensitivity in FAST_SENSITIVITIES:
            return self.settings.rediscovery_interval_days_fast
        return self.settings.rediscovery_interval_days

    def due(
        self, last_started_at: datetime | None, now: datetime, sensitivity: FreshnessSensitivity | None
    ) -> bool:
        if last_started_at is None:
            return True
        return now - last_started_at >= timedelta(days=self.interval_days(sensitivity))

    @staticmethod
    def profile_change_requires_rediscovery(diff: ProfileDiff) -> bool:
        touched = [*diff.added, *diff.removed, *(change.field for change in diff.changed)]
        return any(item.startswith(PROFILE_FIELDS) for item in touched)


def new_entrant_events(
    result: DiscoveryResult, known_ids: set[UUID], client_id: UUID, now: datetime
) -> list[DriftEvent]:
    # only new entities with at least medium confidence surface; nothing changes the confirmed set
    events = []
    for candidate in result.candidates:
        primary = candidate.primary_channel_id
        if candidate.id in known_ids or primary is None or candidate.role is CandidateRole.excluded:
            continue
        if candidate.confidence not in MEDIUM_OR_HIGH:
            continue
        events.append(
            DriftEvent(
                client_id=client_id,
                candidate_id=candidate.id,
                channel_id=primary,
                kind="new_entrant",
                detail={"name": candidate.brand_name, "relation": candidate.relation.value},
                detected_at=now,
            )
        )
    return events


class RediscoveryJob:
    """the monthly cron body: re-runs the rediscovery sources and surfaces new entrants as drift events."""

    def __init__(
        self, runner: DiscoveryRunner, session_factory: SessionFactory, policy: RediscoveryPolicy
    ) -> None:
        self.runner = runner
        self.session_factory = session_factory
        self.policy = policy

    async def run(
        self,
        scope: TenantScope,
        now: datetime,
        *,
        own_channel_ids: list[str] | None = None,
        topic_refs: list[TopicRef] | None = None,
        force: bool = False,
    ) -> DiscoveryResult | None:
        async with self.session_factory() as session:
            repository = CompetitorRepository(session, scope)
            last = await repository.runs.last_completed()
            profile = await latest_profile(session, scope)
            known_ids = {candidate.id for candidate in await repository.known()}
        if profile is None:
            return None
        last_started = last.started_at if last is not None else None
        if not force and not self.policy.due(last_started, now, profile.freshness.sensitivity):
            return None
        request = DiscoveryRequest(
            scope=scope,
            trigger="profile_change" if force else "scheduled",
            profile=profile,
            own_channel_ids=own_channel_ids or [],
            topic_refs=topic_refs or [],
            enabled_sources=rediscovery_sources(),
        )
        result = await self.runner.run(request)
        async with self.session_factory() as session:
            await CompetitorRepository(session, scope).add_drift_events(
                new_entrant_events(result, known_ids, scope.client_id, now)
            )
            await session.commit()
        return result
