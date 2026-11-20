from collections.abc import Collection, Mapping, Sequence
from datetime import date, datetime, timedelta
from typing import Literal

from app.core.config import RefreshSettings
from app.domain.common import DomainModel, VideoId
from app.domain.news import NewsSignal
from app.domain.refresh import DescriptionRefresh, InsertionKind, InsertionOp, RefreshStatus
from app.domain.youtube import VideoRecord
from app.services.refresh.inputs import StoredRefresh
from app.services.refresh.ops.apply import AnchorNotFound, resolve_offset
from app.services.refresh.text import KeywordPresence, content_tokens, normalise, tokens

APPLIED = frozenset({RefreshStatus.applied_claimed, RefreshStatus.applied_detected})


class RefreshAlert(DomainModel):
    kind: Literal["correction", "caution", "not_visible", "applied_detected"]
    refresh_id: str
    video_id: VideoId
    message: str


class StatusChange(DomainModel):
    refresh_id: str
    video_id: VideoId
    from_status: RefreshStatus
    to_status: RefreshStatus
    reason: str
    applied_at: date | None = None
    applied_source: Literal["user", "detected"] | None = None


class LifecycleOutcome(DomainModel):
    changes: list[StatusChange] = []
    alerts: list[RefreshAlert] = []


def _op_visible(op: InsertionOp, description: str, overlap: float) -> bool:
    if op.kind is InsertionKind.link_fix:
        return op.text in description and (op.old_text or "") not in description
    presence = KeywordPresence(description)
    if op.keywords_added and all(presence.contains(keyword) for keyword in op.keywords_added):
        return True
    wanted = set(content_tokens(op.text))
    if not wanted:
        return op.text in description
    return len(wanted & set(tokens(description))) / len(wanted) >= overlap


def detect_applied(refresh: DescriptionRefresh, description: str, overlap: float) -> bool:
    """the user pasted it, even partially or reworded, when every change is now visible."""
    return bool(refresh.operations) and all(
        _op_visible(op, description, overlap) for op in refresh.operations
    )


def _news_lines(refresh: DescriptionRefresh) -> dict[str, str]:
    lines = {}
    for trigger in refresh.triggers:
        if trigger.kind != "news_entity" or trigger.keyword is None:
            continue
        for op in refresh.operations:
            if trigger.id in op.trigger_ids:
                lines[normalise(trigger.keyword)] = op.text
    return lines


class RefreshLifecycle:
    """expiry, withdrawal, retraction alerts, supersession and applied detection for stored suggestions."""

    def __init__(self, settings: RefreshSettings) -> None:
        self.settings = settings

    def reconcile(
        self,
        history: Sequence[StoredRefresh],
        *,
        videos: Mapping[str, VideoRecord],
        signals: Sequence[NewsSignal],
        regenerated_video_ids: Collection[str],
        now: datetime,
    ) -> LifecycleOutcome:
        by_entity = {normalise(signal.entity): signal for signal in signals}
        changes: list[StatusChange] = []
        alerts: list[RefreshAlert] = []
        today = now.date()
        for stored in history:
            refresh = stored.refresh
            if refresh.status is RefreshStatus.proposed:
                change = self._proposed(
                    stored, videos.get(refresh.video_id), by_entity, regenerated_video_ids, now
                )
                if change is None:
                    continue
                changes.append(change)
                if change.to_status is RefreshStatus.applied_detected:
                    report_on = today + timedelta(days=self.settings.impact_window_days)
                    alerts.append(
                        RefreshAlert(
                            kind="applied_detected",
                            refresh_id=refresh.id,
                            video_id=refresh.video_id,
                            message=(
                                f"Looks like you applied this on {today:%d %b}. "
                                f"We'll report the effect on {report_on:%d %b}."
                            ),
                        )
                    )
            elif refresh.status in APPLIED:
                alerts += self._applied_alerts(stored, videos.get(refresh.video_id), by_entity, today)
        return LifecycleOutcome(changes=changes, alerts=alerts)

    def _proposed(
        self,
        stored: StoredRefresh,
        video: VideoRecord | None,
        by_entity: Mapping[str, NewsSignal],
        regenerated: Collection[str],
        now: datetime,
    ) -> StatusChange | None:
        refresh = stored.refresh

        def change(to: RefreshStatus, reason: str, **extra) -> StatusChange:
            return StatusChange(
                refresh_id=refresh.id,
                video_id=refresh.video_id,
                from_status=refresh.status,
                to_status=to,
                reason=reason,
                **extra,
            )

        if refresh.expires_at <= now:
            return change(RefreshStatus.expired, "This suggestion expired with its evidence.")
        if video is None:
            # not loaded this run is not the same as gone; expiry still applies
            return None
        if video.privacy_status != "public" or video.availability != "available":
            return change(
                RefreshStatus.withdrawn, "This video is no longer public, so we removed the suggestion."
            )
        for entity in _news_lines(refresh):
            signal = by_entity.get(entity)
            if signal is None:
                continue
            if signal.verification_status == "retracted":
                return change(RefreshStatus.withdrawn, f"The news about {signal.entity} was retracted.")
            if signal.verification_status == "disputed":
                return change(
                    RefreshStatus.withdrawn,
                    f"Reports about {signal.entity} are now disputed. We've paused this suggestion.",
                )
            if signal.verification_status == "superseded":
                return change(
                    RefreshStatus.superseded,
                    "Updated: a newer release replaces the one we suggested earlier.",
                )
        if video.description_hash != refresh.description_hash_at_generation:
            if detect_applied(refresh, video.description, self.settings.applied_token_overlap):
                return change(
                    RefreshStatus.applied_detected,
                    "The new line is now in the public description.",
                    applied_at=now.date(),
                    applied_source="detected",
                )
            if not self._still_anchored(refresh, video.description):
                return change(
                    RefreshStatus.superseded,
                    "Your description changed since we made this suggestion; we've updated it.",
                )
        if refresh.video_id in regenerated:
            return change(RefreshStatus.superseded, "Replaced by a newer suggestion for this video.")
        return None

    @staticmethod
    def _still_anchored(refresh: DescriptionRefresh, description: str) -> bool:
        try:
            for op in refresh.operations:
                resolve_offset(description, op)
        except AnchorNotFound:
            return False
        return True

    def _applied_alerts(
        self,
        stored: StoredRefresh,
        video: VideoRecord | None,
        by_entity: Mapping[str, NewsSignal],
        today: date,
    ) -> list[RefreshAlert]:
        refresh = stored.refresh
        alerts = []
        applied_on = stored.applied_at or refresh.generated_at.date()
        for entity, line in _news_lines(refresh).items():
            signal = by_entity.get(entity)
            if signal is None:
                continue
            source = signal.sources[0].publisher if signal.sources else "the original source"
            if signal.verification_status == "retracted":
                alerts.append(
                    RefreshAlert(
                        kind="correction",
                        refresh_id=refresh.id,
                        video_id=refresh.video_id,
                        message=(
                            f"Heads-up: the news you added on {applied_on:%d %b} ({signal.entity}) was retracted "
                            f"per {source}. Consider removing: '{line}'"
                        ),
                    )
                )
            elif signal.verification_status == "disputed":
                alerts.append(
                    RefreshAlert(
                        kind="caution",
                        refresh_id=refresh.id,
                        video_id=refresh.video_id,
                        message=f"Reports about {signal.entity} are now disputed. Consider reviewing: '{line}'",
                    )
                )
        grace = timedelta(days=self.settings.applied_visible_grace_days)
        if (
            refresh.status is RefreshStatus.applied_claimed
            and video is not None
            and stored.applied_at is not None
            and today - stored.applied_at >= grace
            and not detect_applied(refresh, video.description, self.settings.applied_token_overlap)
        ):
            alerts.append(
                RefreshAlert(
                    kind="not_visible",
                    refresh_id=refresh.id,
                    video_id=refresh.video_id,
                    message="We don't see the new line in the public description yet.",
                )
            )
        return alerts
