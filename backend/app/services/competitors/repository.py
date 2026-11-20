import asyncio
import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionFactory
from app.domain.competitors import (
    BusinessRelevance,
    CandidateRole,
    CandidateSource,
    CandidateStatus,
    ChannelEvidence,
    ChannelMatch,
    CompetitorCandidate,
    CompetitorRelation,
    CompetitorSet,
    DriftEvent,
    GeoRef,
    OwnerKind,
    YouTubeRelevance,
)
from app.domain.youtube import OwnershipLabel, OwnershipSignal, OwnershipVerdict
from app.models.competitors import BrandChannelResolution
from app.models.competitors import CompetitorCandidate as CandidateRow
from app.models.competitors import CompetitorCandidateSource as SourceRow
from app.models.competitors import CompetitorChannelMatch as MatchRow
from app.models.competitors import CompetitorDiscoveryRun as RunRow
from app.models.competitors import CompetitorDriftEvent as DriftRow
from app.models.competitors import CompetitorSet as SetRow
from app.repositories.tenant.base import TenantRepository
from app.services.competitors.model import DiscoveryResult, DiscoveryUsage, DiscoveryWarning, KnownCandidate
from app.services.competitors.resolve.resolution_cache import CachedResolution, KeyType

ROLE_TO_DB = {
    CandidateRole.competitor_set: "competitor",
    CandidateRole.niche_set: "niche",
    CandidateRole.benchmark_set: "benchmark",
    CandidateRole.creator_seed: "creator",
    CandidateRole.excluded: None,
}
ROLE_FROM_DB = {value: role for role, value in ROLE_TO_DB.items()}
# set by the user on a secondary channel; survives rediscovery
INCLUDED_FLAG = "included_by_user"


class DiscoveryRunRepository(TenantRepository[RunRow]):
    model = RunRow

    async def start(self, trigger: str, pipeline_run_id: uuid.UUID | None, started_at: datetime) -> uuid.UUID:
        row = self.add(
            RunRow(id=uuid.uuid4(), run_id=pipeline_run_id, trigger=trigger, started_at=started_at)
        )
        await self.session.flush()
        return row.id

    async def finish(
        self,
        run_id: uuid.UUID,
        *,
        status: str,
        usage: DiscoveryUsage,
        warnings: list[DiscoveryWarning],
        finished_at: datetime,
        error: str | None = None,
    ) -> None:
        row = await self.get(run_id)
        if row is None:
            raise LookupError(f"discovery run {run_id} not found")
        row.status = status
        row.finished_at = finished_at
        row.yt_units = usage.yt_units
        row.search_calls = usage.search_calls
        row.tavily_credits = round(usage.tavily_credits)
        row.llm_tokens_in = usage.llm_tokens_in
        row.llm_tokens_out = usage.llm_tokens_out
        row.llm_usd = Decimal(str(round(usage.llm_usd, 4)))
        row.warnings = [warning.model_dump() for warning in warnings]
        row.error = error

    async def last_completed(self) -> RunRow | None:
        statement = self._scoped(select(RunRow).where(RunRow.status.in_(("completed", "partial"))))
        statement = statement.order_by(RunRow.started_at.desc()).limit(1)
        return (await self.session.execute(statement)).scalar_one_or_none()


class CandidateRepository(TenantRepository[CandidateRow]):
    model = CandidateRow

    async def all_rows(self) -> Sequence[CandidateRow]:
        return (await self.session.execute(self._scoped(select(CandidateRow)))).scalars().all()


class CandidateSourceRepository(TenantRepository[SourceRow]):
    model = SourceRow


class ChannelMatchRepository(TenantRepository[MatchRow]):
    model = MatchRow

    async def for_candidates(self, candidate_ids: Sequence[uuid.UUID]) -> Sequence[MatchRow]:
        statement = self._scoped(select(MatchRow).where(MatchRow.candidate_id.in_(candidate_ids)))
        return (await self.session.execute(statement)).scalars().all()


class CompetitorSetRepository(TenantRepository[SetRow]):
    model = SetRow

    async def latest(self) -> SetRow | None:
        statement = self._scoped(select(SetRow)).order_by(SetRow.version.desc()).limit(1)
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def next_version(self) -> int:
        statement = self._scoped(select(func.coalesce(func.max(SetRow.version), 0)))
        return int((await self.session.execute(statement)).scalar_one()) + 1


class DriftEventRepository(TenantRepository[DriftRow]):
    model = DriftRow

    async def open_events(self) -> Sequence[DriftRow]:
        statement = self._scoped(select(DriftRow).where(DriftRow.acknowledged_at.is_(None)))
        return (await self.session.execute(statement.order_by(DriftRow.detected_at.desc()))).scalars().all()

    async def acknowledge(self, event_id: uuid.UUID, at: datetime) -> bool:
        row = await self.get(event_id)
        if row is None:
            return False
        row.acknowledged_at = at
        return True


def evidence_payload(channel: ChannelMatch) -> dict[str, Any]:
    return {
        "title": channel.title,
        "handle": channel.handle,
        "evidence": channel.youtube.model_dump(mode="json"),
        "relevance": channel.youtube_relevance.model_dump(mode="json"),
    }


def known_from_rows(row: CandidateRow, matches: Sequence[MatchRow]) -> KnownCandidate:
    return KnownCandidate(
        id=row.id,
        brand_name=row.brand_name,
        normalized_name=row.normalized_name,
        official_domain=row.official_domain,
        aliases=list(row.aliases),
        channel_ids=[match.channel_id for match in sorted(matches, key=lambda match: not match.is_primary)],
        status=CandidateStatus(row.status),
        role=ROLE_FROM_DB.get(row.role, CandidateRole.excluded),
        relation=CompetitorRelation(row.relation),
        decided_by=row.decided_by,
        first_seen_run_id=row.first_seen_run_id,
        rejection_reason=row.rejection_reason,
        owner_kind=OwnerKind(row.owner_kind),
    )


def channel_from_row(match: MatchRow) -> ChannelMatch | None:
    # youtube evidence is nulled by retention after 28 days; such matches wait for a refresh
    if not match.youtube_evidence:
        return None
    payload = match.youtube_evidence
    return ChannelMatch(
        channel_id=match.channel_id,
        handle=payload.get("handle"),
        title=payload["title"],
        resolution_method=match.resolution_method,
        variant=match.variant,
        is_primary=match.is_primary,
        ownership=OwnershipVerdict(
            label=OwnershipLabel(match.ownership_label),
            method=match.ownership_method,
            score=match.ownership_score,
            signals=[OwnershipSignal.model_validate(signal) for signal in match.ownership_signals],
            llm_reason=match.llm_reason,
        ),
        youtube=ChannelEvidence.model_validate(payload["evidence"]),
        youtube_relevance=YouTubeRelevance.model_validate(payload["relevance"]),
        flags=list(match.flags),
    )


class CompetitorRepository:
    """persists discovery results; user decisions on a candidate always survive later runs."""

    def __init__(self, session: AsyncSession, scope) -> None:
        self.session = session
        self.scope = scope
        self.runs = DiscoveryRunRepository(session, scope)
        self.candidates = CandidateRepository(session, scope)
        self.sources = CandidateSourceRepository(session, scope)
        self.matches = ChannelMatchRepository(session, scope)
        self.sets = CompetitorSetRepository(session, scope)
        self.drift = DriftEventRepository(session, scope)

    async def known(self) -> list[KnownCandidate]:
        rows = await self.candidates.all_rows()
        matches = await self.matches.for_candidates([row.id for row in rows]) if rows else []
        by_candidate: dict[uuid.UUID, list[MatchRow]] = {}
        for match in matches:
            by_candidate.setdefault(match.candidate_id, []).append(match)
        return [known_from_rows(row, by_candidate.get(row.id, [])) for row in rows]

    async def save_result(self, result: DiscoveryResult, now: datetime) -> dict[uuid.UUID, uuid.UUID]:
        """upserts candidates and returns result candidate id -> stored row id."""
        rows = list(await self.candidates.all_rows())
        stored: dict[uuid.UUID, uuid.UUID] = {}
        taken_names = {row.normalized_name: row for row in rows}
        for candidate in result.candidates:
            row = self._match_row(rows, candidate)
            if row is None:
                name = candidate.normalized_name
                if name in taken_names:
                    countries = sorted({geo.country_iso2 for geo in candidate.geography}) or ["other"]
                    name = f"{name} ({'/'.join(countries)})"
                row = self.candidates.add(
                    CandidateRow(
                        id=candidate.id,
                        brand_name=candidate.brand_name,
                        normalized_name=name,
                        relation=candidate.relation.value,
                        confidence=candidate.confidence,
                        first_seen_run_id=result.discovery_run_id,
                    )
                )
                rows.append(row)
                taken_names[name] = row
            self._apply(row, candidate, result.discovery_run_id)
            stored[candidate.id] = row.id
            await self.session.flush()
            await self._save_sources(row.id, candidate.sources, result.discovery_run_id)
            await self._save_matches(row, candidate.channels)
        await self.session.flush()
        return stored

    @staticmethod
    def _match_row(rows: list[CandidateRow], candidate: CompetitorCandidate) -> CandidateRow | None:
        for row in rows:
            if row.id == candidate.id:
                return row
        for row in rows:
            if candidate.official_domain and row.official_domain == candidate.official_domain:
                return row
        return next((row for row in rows if row.normalized_name == candidate.normalized_name), None)

    @staticmethod
    def _apply(row: CandidateRow, candidate: CompetitorCandidate, run_id: uuid.UUID) -> None:
        user_owned = row.decided_by == "user"
        row.brand_name = candidate.brand_name
        row.aliases = sorted(set(row.aliases or []) | set(candidate.aliases))
        if candidate.official_domain and not row.official_domain:
            row.official_domain = candidate.official_domain
        row.relation = candidate.relation.value
        row.owner_kind = candidate.owner_kind.value
        row.geography = [geo.model_dump(mode="json") for geo in candidate.geography]
        row.business = candidate.business.model_dump(mode="json")
        row.confidence = candidate.confidence
        row.flags = list(candidate.flags)
        row.evidence_summary = candidate.evidence_summary
        row.last_seen_run_id = run_id
        if not user_owned:
            row.status = candidate.status.value
            row.role = ROLE_TO_DB[candidate.role]
            row.decided_by = candidate.decided_by
            row.rejection_reason = candidate.rejection_reason

    async def _save_sources(
        self, candidate_id: uuid.UUID, sources: list[CandidateSource], run_id: uuid.UUID
    ) -> None:
        await self.session.execute(
            delete(SourceRow).where(
                SourceRow.candidate_id == candidate_id,
                SourceRow.discovery_run_id == run_id,
                SourceRow.workspace_id == self.scope.workspace_id,
            )
        )
        for source in sources:
            self.sources.add(
                SourceRow(
                    candidate_id=candidate_id,
                    discovery_run_id=run_id,
                    kind=source.kind,
                    ref=source.ref,
                    snippet=source.snippet,
                    retrieved_at=source.retrieved_at,
                )
            )

    async def _save_matches(self, row: CandidateRow, channels: list[ChannelMatch]) -> None:
        existing = {match.channel_id: match for match in await self.matches.for_candidates([row.id])}
        user_owned = row.decided_by == "user"
        for channel in channels:
            match = existing.get(channel.channel_id)
            if match is None:
                match = self.matches.add(
                    MatchRow(
                        candidate_id=row.id,
                        channel_id=channel.channel_id,
                        resolution_method=channel.resolution_method,
                        ownership_label=channel.ownership.label.value,
                        ownership_score=channel.ownership.score,
                        is_primary=channel.is_primary,
                    )
                )
            elif not user_owned:
                match.is_primary = channel.is_primary
            match.resolution_method = channel.resolution_method
            match.variant = channel.variant
            match.ownership_label = channel.ownership.label.value
            match.ownership_score = channel.ownership.score
            match.ownership_method = channel.ownership.method
            match.ownership_signals = [signal.model_dump(mode="json") for signal in channel.ownership.signals]
            match.llm_reason = channel.ownership.llm_reason
            match.youtube_evidence = evidence_payload(channel)
            match.youtube_fetched_at = channel.youtube.fetched_at
            match.flags = sorted(set(channel.flags) | ({INCLUDED_FLAG} & set(match.flags or [])))

    async def load_candidates(self) -> list[CompetitorCandidate]:
        rows = await self.candidates.all_rows()
        if not rows:
            return []
        ids = [row.id for row in rows]
        matches = await self.matches.for_candidates(ids)
        sources = (
            (
                await self.session.execute(
                    self.sources._scoped(select(SourceRow).where(SourceRow.candidate_id.in_(ids)))
                )
            )
            .scalars()
            .all()
        )
        channels: dict[uuid.UUID, list[ChannelMatch]] = {}
        for match in matches:
            if (channel := channel_from_row(match)) is not None:
                channels.setdefault(match.candidate_id, []).append(channel)
        by_candidate: dict[uuid.UUID, list[CandidateSource]] = {}
        for source in sorted(sources, key=lambda source: source.retrieved_at):
            by_candidate.setdefault(source.candidate_id, []).append(
                CandidateSource(
                    kind=source.kind, ref=source.ref, snippet=source.snippet, retrieved_at=source.retrieved_at
                )
            )
        return [self._candidate(row, by_candidate.get(row.id, []), channels.get(row.id, [])) for row in rows]

    @staticmethod
    def _candidate(
        row: CandidateRow, sources: list[CandidateSource], channels: list[ChannelMatch]
    ) -> CompetitorCandidate:
        first_seen = row.first_seen_run_id or row.last_seen_run_id or row.id
        primary_seen = False
        unique_primary = []
        for channel in sorted(channels, key=lambda channel: not channel.is_primary):
            if channel.is_primary and primary_seen:
                channel = channel.model_copy(update={"is_primary": False})
            primary_seen = primary_seen or channel.is_primary
            unique_primary.append(channel)
        return CompetitorCandidate(
            id=row.id,
            client_id=row.client_id,
            brand_name=row.brand_name,
            normalized_name=row.normalized_name,
            aliases=list(row.aliases),
            official_domain=row.official_domain,
            relation=CompetitorRelation(row.relation),
            owner_kind=OwnerKind(row.owner_kind),
            role=ROLE_FROM_DB.get(row.role, CandidateRole.excluded),
            geography=[GeoRef.model_validate(geo) for geo in row.geography],
            sources=sources,
            business=BusinessRelevance.model_validate(row.business),
            channels=unique_primary,
            confidence=row.confidence,
            status=CandidateStatus(row.status),
            flags=list(row.flags),
            evidence_summary=row.evidence_summary or "",
            decided_by=row.decided_by,
            decided_at=row.decided_at,
            rejection_reason=row.rejection_reason,
            first_seen_run_id=first_seen,
            last_seen_run_id=row.last_seen_run_id or first_seen,
        )

    async def save_set(self, competitor_set: CompetitorSet) -> None:
        self.sets.add(
            SetRow(
                version=competitor_set.version,
                competitor_channel_ids=list(competitor_set.competitor_channel_ids),
                niche_channel_ids=list(competitor_set.niche_channel_ids),
                benchmark_channel_ids=list(competitor_set.benchmark_channel_ids),
                creator_seed_channel_ids=list(competitor_set.creator_seed_channel_ids),
                contains_content_competitors=competitor_set.contains_content_competitors,
                confirmed_by=competitor_set.confirmed_by,
                confirmed_at=competitor_set.confirmed_at,
                ids_verified_at=competitor_set.confirmed_at,
            )
        )
        await self.session.flush()

    async def active_set(self) -> CompetitorSet | None:
        row = await self.sets.latest()
        if row is None:
            return None
        return CompetitorSet(
            client_id=row.client_id,
            version=row.version,
            competitor_channel_ids=list(row.competitor_channel_ids),
            niche_channel_ids=list(row.niche_channel_ids),
            benchmark_channel_ids=list(row.benchmark_channel_ids),
            creator_seed_channel_ids=list(row.creator_seed_channel_ids),
            contains_content_competitors=row.contains_content_competitors,
            confirmed_by=row.confirmed_by,
            confirmed_at=row.confirmed_at,
        )

    async def add_drift_events(self, events: list[DriftEvent]) -> int:
        open_keys = {(row.candidate_id, row.channel_id, row.kind) for row in await self.drift.open_events()}
        added = 0
        for event in events:
            if (event.candidate_id, event.channel_id, event.kind) in open_keys:
                continue
            self.drift.add(
                DriftRow(
                    candidate_id=event.candidate_id,
                    channel_id=event.channel_id,
                    kind=event.kind,
                    detail=event.detail,
                    detected_at=event.detected_at,
                )
            )
            open_keys.add((event.candidate_id, event.channel_id, event.kind))
            added += 1
        await self.session.flush()
        return added

    async def open_drift_events(self) -> list[DriftEvent]:
        return [
            DriftEvent(
                client_id=row.client_id,
                candidate_id=row.candidate_id,
                channel_id=row.channel_id,
                kind=row.kind,
                detail=row.detail,
                detected_at=row.detected_at,
                acknowledged_at=row.acknowledged_at,
            )
            for row in await self.drift.open_events()
        ]


class SqlBrandChannelCache:
    """cross-client cache of public brand-to-channel verdicts (brand_channel_resolutions)."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self.session_factory = session_factory
        # resolvers run concurrently; one short lookup at a time keeps this to a single connection
        self._lock = asyncio.Lock()

    async def lookup(self, keys: list[tuple[KeyType, str]], now: datetime) -> list[CachedResolution]:
        if not keys:
            return []
        async with self._lock, self.session_factory() as session:
            statement = select(BrandChannelResolution).where(
                tuple_(BrandChannelResolution.key_type, BrandChannelResolution.key).in_(keys),
                BrandChannelResolution.expires_at > now,
            )
            rows = (await session.execute(statement)).scalars().all()
        return [
            CachedResolution(
                key_type=row.key_type,
                key=row.key,
                channel_id=row.channel_id,
                label=OwnershipLabel(row.ownership_label),
                score=row.ownership_score,
                method=row.ownership_method,
                signals=[OwnershipSignal.model_validate(signal) for signal in row.signals],
                verified_at=row.verified_at,
            )
            for row in sorted(rows, key=lambda row: -row.ownership_score)
        ]

    async def store(self, entries: list[CachedResolution], ttl_days: int) -> None:
        if not entries:
            return
        unique = {(entry.key_type, entry.key, entry.channel_id): entry for entry in entries}
        values = [
            {
                "key_type": entry.key_type,
                "key": entry.key,
                "channel_id": entry.channel_id,
                "ownership_label": entry.label.value,
                "ownership_score": entry.score,
                "ownership_method": entry.method,
                "signals": [signal.model_dump(mode="json") for signal in entry.signals],
                "verified_at": entry.verified_at,
                "expires_at": entry.verified_at + timedelta(days=ttl_days),
            }
            for entry in unique.values()
        ]
        statement = insert(BrandChannelResolution).values(values)
        statement = statement.on_conflict_do_update(
            index_elements=["key_type", "key", "channel_id"],
            set_={
                name: statement.excluded[name]
                for name in values[0]
                if name not in {"key_type", "key", "channel_id"}
            },
        )
        async with self._lock, self.session_factory() as session:
            await session.execute(statement)
            await session.commit()


async def mark_tracked(session: AsyncSession, scope, channel_ids: Sequence[str]) -> None:
    statement = (
        update(MatchRow)
        .where(MatchRow.workspace_id == scope.workspace_id, MatchRow.client_id == scope.client_id)
        .values(is_tracked=MatchRow.channel_id.in_(list(channel_ids)))
    )
    await session.execute(statement)
