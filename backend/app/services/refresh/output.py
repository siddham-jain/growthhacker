from uuid import UUID

from pydantic import AwareDatetime

from app.domain.common import DomainModel, VideoId
from app.domain.recommendations import RecommendationDraft
from app.domain.refresh import DescriptionRefresh, RefreshEligibility, RefreshImpact
from app.services.refresh.eligibility import VideoEligibility
from app.services.refresh.lifecycle import RefreshAlert, StatusChange


class DroppedSuggestion(DomainModel):
    video_id: VideoId
    trigger_kind: str
    keyword: str | None = None
    reason: str


class HygieneReport(DomainModel):
    # the "Channel hygiene" surface: rule-based fixes, kept apart from recommendation cards (99 C19)
    videos_checked: int
    with_booking_link: int
    long_videos: int
    with_valid_chapters: int
    malformed_links: int
    dead_links: int
    refresh_ids: list[str] = []
    summary: list[str] = []


class RefreshOutput(DomainModel):
    client_id: UUID
    run_id: UUID
    generated_at: AwareDatetime
    needs_refresh: bool
    headline: str
    eligibility: RefreshEligibility
    keyword_checks_ran: bool
    videos: list[VideoEligibility] = []
    refreshes: list[DescriptionRefresh] = []
    recommendation_drafts: list[RecommendationDraft] = []
    hygiene: HygieneReport
    messages: list[str] = []
    alerts: list[RefreshAlert] = []
    status_changes: list[StatusChange] = []
    impacts: list[RefreshImpact] = []
    refetch_video_ids: list[VideoId] = []
    dropped: list[DroppedSuggestion] = []
    counts: dict[str, int] = {}

    def surface_of(self, refresh_id: str) -> str:
        return "channel_hygiene" if refresh_id in self.hygiene.refresh_ids else "recommendations"
