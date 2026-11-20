from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import CompetitorSettings
from app.core.tenancy import TenantScope
from app.domain.competitors import CandidateStatus, DriftEvent
from app.domain.topics import TopicRef
from app.domain.youtube import OwnershipSignal
from app.models.competitors import CompetitorCandidateSource as SourceRow
from app.models.competitors import CompetitorChannelMatch as MatchRow
from app.repositories.shared.youtube import ChannelRepository, VideoRepository
from app.services.competitors.drift.detector import MARKER_PREFIX, DriftDetector
from app.services.competitors.drift.rules import ChannelSnapshot, DriftContext, TrackedChannel
from app.services.competitors.repository import CompetitorRepository
from app.services.competitors.score.topics import PhraseTopicMatcher
from app.services.competitors.score.youtube import YouTubeRelevanceFacts

CLOSED = frozenset({CandidateStatus.rejected.value, CandidateStatus.excluded.value})
RECENT_UPLOADS = 50


class CompetitorDriftMonitor:
    """reads 02's cached channel rows and our own match history; zero youtube calls."""

    def __init__(self, session: AsyncSession, scope: TenantScope, settings: CompetitorSettings) -> None:
        self.session = session
        self.scope = scope
        self.settings = settings
        self.repository = CompetitorRepository(session, scope)
        self.detector = DriftDetector()

    async def run(self, now: datetime, topic_refs: list[TopicRef]) -> tuple[list[DriftEvent], int]:
        active = await self.repository.active_set()
        in_set = set()
        if active is not None:
            in_set = {
                *active.competitor_channel_ids,
                *active.niche_channel_ids,
                *active.benchmark_channel_ids,
                *active.creator_seed_channel_ids,
            }
        rows = {row.id: row for row in await self.repository.candidates.all_rows()}
        matches = await self.repository.matches.for_candidates(list(rows)) if rows else []
        niche_counts = await self._recent_niche_queries(now)
        tracked: list[tuple[TrackedChannel, MatchRow]] = []
        for match in matches:
            row = rows[match.candidate_id]
            watched = match.channel_id in in_set
            if not watched and (not match.is_primary or row.status in CLOSED):
                continue
            tracked.append(
                (self._tracked(match, row.official_domain, watched, niche_counts.get(row.id, 0)), match)
            )
        ctx = DriftContext(
            client_id=self.scope.client_id,
            now=now,
            settings=self.settings,
            matcher=PhraseTopicMatcher.from_topic_refs(topic_refs),
            latest=await self._snapshots([item.channel_id for item, _ in tracked if item.in_set], now),
        )
        channels = [item for item, _ in tracked]
        events = self.detector.detect(channels, ctx)
        for item, match in tracked:
            markers = self.detector.overlap_markers(item, ctx)
            match.flags = sorted(
                {flag for flag in match.flags if not flag.startswith(MARKER_PREFIX)} | markers
            )
        await self.repository.add_drift_events(events)
        return events, len(channels)

    @staticmethod
    def _tracked(match: MatchRow, domain: str | None, in_set: bool, niche_queries: int) -> TrackedChannel:
        payload = match.youtube_evidence or {}
        relevance = payload.get("relevance")
        return TrackedChannel(
            candidate_id=match.candidate_id,
            channel_id=match.channel_id,
            in_set=in_set,
            title=payload.get("title", ""),
            handle=payload.get("handle"),
            official_domain=domain,
            signals=tuple(OwnershipSignal.model_validate(signal) for signal in match.ownership_signals),
            relevance=YouTubeRelevanceFacts.model_validate(relevance) if relevance else None,
            flags=frozenset(match.flags),
            niche_queries_recent=niche_queries,
        )

    async def _snapshots(self, channel_ids: list[str], now: datetime) -> dict[str, ChannelSnapshot]:
        if not channel_ids:
            return {}
        channels = await ChannelRepository(self.session).get_many(channel_ids)
        videos = VideoRepository(self.session)
        since = now - timedelta(days=90)
        snapshots = {}
        for channel in channels:
            recent = (await videos.list_for_channel(channel.channel_id, published_after=since))[
                :RECENT_UPLOADS
            ]
            snapshots[channel.channel_id] = ChannelSnapshot(
                title=channel.title or "",
                handle=channel.handle,
                description=channel.description or "",
                availability=channel.availability,
                last_upload_at=channel.last_upload_at,
                recent_texts=tuple(f"{video.title or ''} {video.description or ''}" for video in recent),
            )
        return snapshots

    async def _recent_niche_queries(self, now: datetime) -> dict[UUID, int]:
        since = now - timedelta(days=self.settings.niche_riser_window_days)
        statement = select(SourceRow.candidate_id, SourceRow.ref).where(
            SourceRow.workspace_id == self.scope.workspace_id,
            SourceRow.client_id == self.scope.client_id,
            SourceRow.kind == "niche_search",
            SourceRow.retrieved_at >= since,
        )
        queries: dict[UUID, set[str]] = {}
        for candidate_id, ref in (await self.session.execute(statement)).all():
            queries.setdefault(candidate_id, set()).update(
                part.strip() for part in ref.split(" ; ") if part.strip()
            )
        return {candidate_id: len(found) for candidate_id, found in queries.items()}
