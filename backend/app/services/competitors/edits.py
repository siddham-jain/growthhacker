import uuid
from datetime import datetime

from app.core.config import CompetitorSettings
from app.domain.competitors import CandidateRole, CandidateStatus, CompetitorSet
from app.domain.youtube import OwnershipLabel
from app.models.competitors import CompetitorCandidate as CandidateRow
from app.models.competitors import CompetitorChannelMatch as MatchRow
from app.services.competitors.context import DiscoveryContext
from app.services.competitors.repository import (
    INCLUDED_FLAG,
    ROLE_FROM_DB,
    ROLE_TO_DB,
    CompetitorRepository,
    mark_tracked,
)
from app.services.competitors.select.validation import (
    CompetitorSetValidator,
    SetSelection,
    SetValidationError,
)
from app.services.competitors.service import CompetitorDiscoveryService

CONFIRMED_STATUSES = frozenset(
    {CandidateStatus.confirmed, CandidateStatus.auto_confirmed, CandidateStatus.user_added}
)
CLOSED_STATUSES = frozenset({CandidateStatus.rejected, CandidateStatus.excluded})
OWN_GROUP_RELATIONS = frozenset({"self", "subsidiary", "parent"})


class CandidateNotFound(LookupError):
    pass


class CompetitorEditService:
    """user decisions on candidates; every edit is stamped decided_by=user so rediscovery never undoes it."""

    def __init__(self, repository: CompetitorRepository, settings: CompetitorSettings) -> None:
        self.repository = repository
        self.settings = settings
        self.validator = CompetitorSetValidator(settings)

    async def confirm(self, candidate_id: uuid.UUID, now: datetime) -> CandidateRow:
        row = await self._row(candidate_id)
        if row.relation in OWN_GROUP_RELATIONS:
            raise SetValidationError("own_channel", "That looks like your own channel.")
        if ROLE_FROM_DB.get(row.role) is CandidateRole.competitor_set:
            confirmed = [
                other
                for other in await self.repository.candidates.all_rows()
                if other.id != row.id
                and other.role == "competitor"
                and CandidateStatus(other.status) in CONFIRMED_STATUSES
            ]
            if len(confirmed) >= self.settings.max_competitors:
                raise SetValidationError(
                    "competitors_out_of_range",
                    f"Pick between {self.settings.min_competitors} and {self.settings.max_competitors} competitors "
                    f"({len(confirmed) + 1} selected).",
                )
        return self._decide(row, now, status=CandidateStatus.confirmed)

    async def add(
        self, service: CompetitorDiscoveryService, ctx: DiscoveryContext, now: datetime
    ) -> list[CandidateRow]:
        """the single-entity path behind 'add competitor': user source only, then the normal resolution chain."""
        ctx.enabled_sources = frozenset({"user"})
        result = await service.discover(ctx)
        stored = await self.repository.save_result(result, now)
        added = []
        for candidate in result.candidates:
            if "user" not in {source.kind for source in candidate.sources}:
                continue
            row = await self._row(stored[candidate.id])
            if row.relation in OWN_GROUP_RELATIONS:
                raise SetValidationError("own_channel", "That looks like your own channel.")
            role = ctx.user_inputs[0].intended_role if ctx.user_inputs else CandidateRole.competitor_set
            row.role = ROLE_TO_DB[role]
            added.append(self._decide(row, now, status=CandidateStatus.user_added, rejection_reason=None))
        return added

    async def reject(self, candidate_id: uuid.UUID, reason: str | None, now: datetime) -> CandidateRow:
        row = await self._row(candidate_id)
        return self._decide(
            row, now, status=CandidateStatus.rejected, rejection_reason=reason or "rejected by user"
        )

    async def set_role(self, candidate_id: uuid.UUID, role: CandidateRole, now: datetime) -> CandidateRow:
        # one role per candidate, so moving between sets never duplicates a channel
        row = await self._row(candidate_id)
        if role is not CandidateRole.excluded and row.relation in OWN_GROUP_RELATIONS:
            raise SetValidationError("own_channel", "That looks like your own channel.")
        row.role = ROLE_TO_DB[role]
        return self._decide(row, now)

    async def choose_primary(self, candidate_id: uuid.UUID, channel_id: str, now: datetime) -> CandidateRow:
        row = await self._row(candidate_id)
        matches = await self.repository.matches.for_candidates([row.id])
        if channel_id not in {match.channel_id for match in matches}:
            raise SetValidationError("channel_unknown", "That channel is not linked to this competitor.")
        for match in matches:
            match.is_primary = match.channel_id == channel_id
        return self._decide(row, now)

    async def include_channel(
        self, candidate_id: uuid.UUID, channel_id: str, include: bool, now: datetime
    ) -> CandidateRow:
        row = await self._row(candidate_id)
        match = next(
            (m for m in await self.repository.matches.for_candidates([row.id]) if m.channel_id == channel_id),
            None,
        )
        if match is None:
            raise SetValidationError("channel_unknown", "That channel is not linked to this competitor.")
        flags = set(match.flags) - {INCLUDED_FLAG}
        match.flags = sorted(flags | ({INCLUDED_FLAG} if include else set()))
        return self._decide(row, now)

    async def freeze(
        self,
        now: datetime,
        *,
        own_channel_ids: frozenset[str] = frozenset(),
        acknowledge_uncertain: bool = False,
        confirmed_by: str = "user",
    ) -> CompetitorSet:
        rows = await self.repository.candidates.all_rows()
        matches = await self.repository.matches.for_candidates([row.id for row in rows]) if rows else []
        by_candidate: dict[uuid.UUID, list[MatchRow]] = {}
        for match in matches:
            by_candidate.setdefault(match.candidate_id, []).append(match)
        lists: dict[CandidateRole, list[str]] = {role: [] for role in CandidateRole}
        own = set(own_channel_ids)
        uncertain, inactive = set(), set()
        contains_content = False
        for row in rows:
            row_matches = by_candidate.get(row.id, [])
            if row.relation in OWN_GROUP_RELATIONS:
                own.update(match.channel_id for match in row_matches)
                continue
            status, role = CandidateStatus(row.status), ROLE_FROM_DB.get(row.role, CandidateRole.excluded)
            if status in CLOSED_STATUSES or role is CandidateRole.excluded:
                continue
            chosen = self._tracked_matches(row_matches, status, role)
            for match in chosen:
                lists[role].append(match.channel_id)
                if match.ownership_label == OwnershipLabel.uncertain.value:
                    uncertain.add(match.channel_id)
                if "inactive" in match.flags:
                    inactive.add(match.channel_id)
            if role is CandidateRole.competitor_set and chosen and row.relation in {"aggregator", "content"}:
                contains_content = True
        selection = SetSelection(
            competitor_ids=lists[CandidateRole.competitor_set],
            niche_ids=lists[CandidateRole.niche_set][: self.settings.niche_max],
            benchmark_ids=lists[CandidateRole.benchmark_set][: self.settings.benchmark_max],
            creator_ids=lists[CandidateRole.creator_seed][: self.settings.creator_seed_max],
            own_ids=frozenset(own),
            uncertain_ids=frozenset(uncertain),
            inactive_ids=frozenset(inactive),
            acknowledge_uncertain=acknowledge_uncertain,
        )
        self.validator.validate(selection)
        competitor_set = CompetitorSet(
            client_id=self.repository.scope.client_id,
            version=await self.repository.sets.next_version(),
            competitor_channel_ids=selection.competitor_ids,
            niche_channel_ids=selection.niche_ids,
            benchmark_channel_ids=selection.benchmark_ids,
            creator_seed_channel_ids=selection.creator_ids,
            contains_content_competitors=contains_content,
            confirmed_by=confirmed_by,
            confirmed_at=now,
        )
        await self.repository.save_set(competitor_set)
        tracked = [
            *selection.competitor_ids,
            *selection.niche_ids,
            *selection.benchmark_ids,
            *selection.creator_ids,
        ]
        await mark_tracked(self.repository.session, self.repository.scope, tracked)
        return competitor_set

    async def auto_confirm(self, competitor_channel_ids: list[str], now: datetime) -> CompetitorSet:
        """demo tenants: mark the proposed competitors auto-confirmed, then freeze; user decisions are kept."""
        rows = await self.repository.candidates.all_rows()
        matches = await self.repository.matches.for_candidates([row.id for row in rows]) if rows else []
        proposed = {
            match.candidate_id
            for match in matches
            if match.is_primary and match.channel_id in competitor_channel_ids
        }
        for row in rows:
            if row.id in proposed and row.decided_by != "user":
                row.status = CandidateStatus.auto_confirmed.value
                row.role = ROLE_TO_DB[CandidateRole.competitor_set]
                row.decided_by = "auto"
                row.decided_at = now
            elif row.role == "competitor" and row.decided_by != "user" and row.id not in proposed:
                row.role = ROLE_TO_DB[CandidateRole.niche_set]
        await self.repository.session.flush()
        return await self.freeze(now, confirmed_by="auto")

    @staticmethod
    def _tracked_matches(
        matches: list[MatchRow], status: CandidateStatus, role: CandidateRole
    ) -> list[MatchRow]:
        primary = [match for match in matches if match.is_primary]
        extras = [match for match in matches if not match.is_primary and INCLUDED_FLAG in match.flags]
        if role is CandidateRole.competitor_set:
            return [*primary, *extras] if status in CONFIRMED_STATUSES else []
        owned = {OwnershipLabel.verified.value, OwnershipLabel.likely.value}
        if status in CONFIRMED_STATUSES:
            return [*primary, *extras]
        return [
            match for match in primary if match.ownership_label in owned and "inactive" not in match.flags
        ]

    async def _row(self, candidate_id: uuid.UUID) -> CandidateRow:
        row = await self.repository.candidates.get(candidate_id)
        if row is None:
            raise CandidateNotFound(f"competitor candidate {candidate_id} not found")
        return row

    @staticmethod
    def _decide(row: CandidateRow, now: datetime, **changes) -> CandidateRow:
        for name, value in changes.items():
            setattr(row, name, value.value if isinstance(value, CandidateStatus) else value)
        row.decided_by = "user"
        row.decided_at = now
        return row
