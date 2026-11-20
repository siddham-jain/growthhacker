from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.core.config import CompetitorSettings
from app.domain.common import subscriber_band
from app.domain.competitors import ChannelEvidence, TopicUploadMatch, YouTubeRelevance
from app.services.competitors.model import ChannelLead
from app.services.competitors.resolve.evidence import channel_evidence
from app.services.competitors.score.topics import LexicalTopicMatcher

YouTubeTier = Literal["high", "medium", "low"]
MAX_COVERAGE_REFS = 20


@dataclass(frozen=True)
class YouTubeAssessment:
    # api-derived only; tier and the raw matching-upload count are kept apart from web relevance
    evidence: ChannelEvidence
    relevance: YouTubeRelevance
    matching_uploads: int
    tier: YouTubeTier


def size_band(subscribers: int | None, client_subscribers: int | None) -> str:
    if not subscribers or not client_subscribers:
        return "unknown"
    ratio = subscribers / client_subscribers
    if ratio < 0.02:
        return "much_smaller"
    if ratio < 0.2:
        return "smaller"
    if ratio <= 5:
        return "peer"
    if ratio <= 50:
        return "larger"
    return "much_larger"


class YouTubeRelevanceScorer:
    def __init__(
        self, matcher: LexicalTopicMatcher, settings: CompetitorSettings, client_subscribers: int | None
    ) -> None:
        self.matcher = matcher
        self.settings = settings
        self.client_subscribers = client_subscribers

    def assess(self, lead: ChannelLead, now: datetime) -> YouTubeAssessment | None:
        evidence = channel_evidence(lead, now)
        if evidence is None:
            return None
        uploads = lead.uploads or []
        per_ref: Counter[str] = Counter()
        matching = 0
        for video in uploads:
            matched = self.matcher.match(f"{video.title} {video.description}")
            matching += bool(matched)
            per_ref.update(matched)
        share = matching / len(uploads) if uploads else 0.0
        denominator = min(MAX_COVERAGE_REFS, self.matcher.size)
        coverage = min(len(per_ref) / denominator, 1.0) if denominator else 0.0
        activity = min(1.0, evidence.uploads_90d / self.settings.activity_target_uploads_90d)
        relevance = YouTubeRelevance(
            activity=round(activity, 4),
            content_overlap=round(0.5 * share + 0.5 * coverage, 4),
            matched_topic_refs=[
                TopicUploadMatch(topic_ref_id=ref_id, upload_matches=count)
                for ref_id, count in per_ref.most_common()
            ],
            size_band=size_band(evidence.subscriber_count, self.client_subscribers),
            subscriber_band=subscriber_band(evidence.subscriber_count),
        )
        return YouTubeAssessment(
            evidence=evidence,
            relevance=relevance,
            matching_uploads=matching,
            tier=self.tier(relevance, evidence),
        )

    def tier(self, relevance: YouTubeRelevance, evidence: ChannelEvidence) -> YouTubeTier:
        settings = self.settings
        if (
            relevance.activity >= settings.youtube_tier_high_activity
            and relevance.content_overlap >= settings.youtube_tier_high_overlap
        ):
            return "high"
        if evidence.uploads_90d > 0 and relevance.content_overlap >= settings.youtube_tier_medium_overlap:
            return "medium"
        return "low"
