from collections import Counter

from app.core.config import CompetitorSettings
from app.domain.youtube import OwnershipLabel
from app.services.competitors.model import CandidateEntity, ChannelLead, LeadRef, LinkEvidence
from app.services.competitors.youtube_access import MeteredYouTube

EVIDENCE_PRIORITY = {
    "user_asserted": 0,
    "site_links_channel": 1,
    "jsonld_sameas": 1,
    "embed_majority": 2,
    "websearch_youtube_hit": 3,
}


LABEL_PRIORITY = {OwnershipLabel.verified: 0, OwnershipLabel.likely: 1, None: 2, OwnershipLabel.uncertain: 3}


def lead_priority(lead: ChannelLead) -> tuple[int, int, int]:
    evidence_rank = min((EVIDENCE_PRIORITY.get(item.name, 5) for item in lead.evidence), default=5)
    return LABEL_PRIORITY.get(lead.label, 4), evidence_rank, int(lead.guessed)


class LeadMaterialiser:
    """turns channel references (ids, handles, videos, playlists) into channel leads at minimum cost."""

    def __init__(self, youtube: MeteredYouTube, settings: CompetitorSettings) -> None:
        self.youtube = youtube
        self.settings = settings

    async def apply(self, found: list[tuple[CandidateEntity, list[LeadRef]]]) -> None:
        video_ids = [ref.value for _, refs in found for ref in refs if ref.kind == "video"]
        videos = await self.youtube.videos(video_ids) if video_ids else {}
        owners = {video_id: video.channel_id for video_id, video in videos.items()}
        for entity, refs in found:
            self._embed_majority(entity, refs, owners)
            for ref in refs:
                if ref.method == "embed_vote":
                    continue
                channel_id = await self._channel_id(ref, owners)
                if channel_id is not None:
                    self._add(entity, channel_id, ref)
            self._cap(entity)

    async def _channel_id(self, ref: LeadRef, owners: dict[str, str]) -> str | None:
        if ref.kind == "channel_id":
            return ref.value
        if ref.kind == "video":
            return owners.get(ref.value)
        if ref.kind == "playlist":
            return await self.youtube.playlist_owner(ref.value)
        if ref.kind == "handle":
            record = await self.youtube.by_handle(ref.value)
        else:
            record = await self.youtube.by_username(ref.value) or await self.youtube.by_handle(ref.value)
        return record.channel_id if record is not None else None

    def _embed_majority(self, entity: CandidateEntity, refs: list[LeadRef], owners: dict[str, str]) -> None:
        embedded = list(dict.fromkeys(ref.value for ref in refs if ref.method == "embed_vote"))
        votes = Counter(owners[video_id] for video_id in embedded if video_id in owners)
        if not votes:
            return
        channel_id, count = votes.most_common(1)[0]
        share = count / sum(votes.values())
        if count < self.settings.embed_min_votes or share < self.settings.embed_majority_share:
            return
        source = next(
            (item.source_url for ref in refs for item in ref.evidence if ref.method == "embed_vote"), None
        )
        detail = f"{count} of {sum(votes.values())} videos embedded on {entity.official_domain} belong to this channel"
        evidence = LinkEvidence(name="embed_majority", detail=detail, source_url=source)
        self._add(
            entity,
            channel_id,
            LeadRef(kind="channel_id", value=channel_id, method="embed_vote", evidence=[evidence]),
        )

    @staticmethod
    def _add(entity: CandidateEntity, channel_id: str, ref: LeadRef) -> None:
        lead = entity.leads.get(channel_id)
        if lead is None:
            lead = ChannelLead(channel_id=channel_id, method=ref.method, guessed=ref.guessed)
            hinted = entity.channel_hints.get(channel_id)
            if hinted is not None:
                lead.record = hinted
            entity.leads[channel_id] = lead
        elif lead.guessed and not ref.guessed:
            lead.guessed, lead.method = False, ref.method
        lead.add_evidence(list(ref.evidence))

    def _cap(self, entity: CandidateEntity) -> None:
        limit = self.settings.max_leads_per_entity
        if len(entity.leads) <= limit:
            return
        keep = sorted(entity.leads.values(), key=lead_priority)[:limit]
        entity.leads = {lead.channel_id: lead for lead in keep}
