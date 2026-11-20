from datetime import datetime, timedelta

from app.core.config import CompetitorSettings
from app.domain.competitors import ChannelEvidence
from app.pipeline.contracts import BudgetExceeded
from app.ports.quota import QuotaExhausted
from app.services.competitors.context import DiscoveryContext
from app.services.competitors.model import CandidateEntity, ChannelLead
from app.services.competitors.youtube_access import MeteredYouTube

RECENT_DAYS = 90


def channel_evidence(lead: ChannelLead, now: datetime) -> ChannelEvidence | None:
    record = lead.record
    if record is None:
        return None
    if lead.upload_items:
        cutoff = now - timedelta(days=RECENT_DAYS)
        uploads_90d = sum(item.published_at >= cutoff for item in lead.upload_items)
        last_upload_at = max(item.published_at for item in lead.upload_items)
        recent_ids = [item.video_id for item in lead.upload_items]
    else:
        uploads_90d, last_upload_at, recent_ids = (
            record.uploads_90d,
            record.last_upload_at,
            record.recent_upload_ids,
        )
    return ChannelEvidence(
        subscriber_count=None if record.hidden_subscriber_count else record.subscriber_count,
        video_count=record.video_count,
        country=record.country,
        published_at=record.published_at,
        uploads_90d=uploads_90d,
        last_upload_at=last_upload_at,
        recent_upload_ids=recent_ids[:50],
        fetched_at=record.metadata_fetched_at,
    )


class EvidenceCollector:
    """batched channel records, then uploads for the leads worth checking; stops cleanly at the budget."""

    def __init__(self, youtube: MeteredYouTube, settings: CompetitorSettings) -> None:
        self.youtube = youtube
        self.settings = settings

    async def channels(self, entities: list[CandidateEntity]) -> None:
        missing = [
            lead.channel_id for entity in entities for lead in entity.leads.values() if lead.record is None
        ]
        records = await self.youtube.channels(missing) if missing else {}
        for entity in entities:
            for channel_id, lead in list(entity.leads.items()):
                if lead.record is None:
                    lead.record = records.get(channel_id)
                if lead.record is None or lead.record.availability == "unavailable":
                    del entity.leads[channel_id]

    async def uploads(self, leads: list[ChannelLead], ctx: DiscoveryContext) -> None:
        sample = self.settings.uploads_sample_size
        todo = [lead for lead in leads if lead.uploads is None and lead.record is not None]
        per_call = max(1, 50 // sample)
        for start in range(0, len(todo), per_call):
            chunk = todo[start : start + per_call]
            try:
                ctx.meter.check_youtube(len(chunk) + 1)
                for lead in chunk:
                    playlist_id = lead.record.uploads_playlist_id
                    page = await self.youtube.uploads(playlist_id) if playlist_id else None
                    lead.upload_items = list(page.items) if page is not None else []
                ids = [video_id for lead in chunk for video_id in self._recent_ids(lead, sample)]
                videos = await self.youtube.videos(ids) if ids else {}
            except (BudgetExceeded, QuotaExhausted):
                for lead in todo[start:]:
                    lead.flags.add("uploads_not_checked")
                ctx.warn("UPLOADS_PARTIAL", "Partial competitor scan. More suggestions will appear tomorrow.")
                return
            for lead in chunk:
                lead.uploads = [
                    videos[video_id] for video_id in self._recent_ids(lead, sample) if video_id in videos
                ]

    @staticmethod
    def _recent_ids(lead: ChannelLead, sample: int) -> list[str]:
        if lead.upload_items:
            return [item.video_id for item in lead.upload_items[:sample]]
        return lead.record.recent_upload_ids[:sample]
