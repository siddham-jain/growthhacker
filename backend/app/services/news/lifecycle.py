import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from app.domain.news import ConfirmationStatus, NewsItem, NewsSignal, SourceTier
from app.domain.recommendations import RecType, make_stable_key, recommendation_id
from app.services.news.text import contains_phrase
from app.services.news.versions import strip_versions, version_key, version_tokens

VerificationStatus = Literal["active", "disputed", "retracted", "superseded"]
STATUS_RANK: dict[VerificationStatus, int] = {"active": 0, "disputed": 1, "superseded": 2, "retracted": 3}
RETRACTION_CUES = re.compile(
    r"\b(retract(s|ed|ion)?|withdr(aw|aws|awn|ew)|walk(s|ed)? back|den(y|ies|ied)|debunk(s|ed)?|hoax"
    r"|not real|false report|fake (news|report|announcement)|correction|cancel(s|led|ed)?|rolled back|recalled)\b",
    re.IGNORECASE,
)
RELIABLE_TIERS = frozenset(
    {SourceTier.tier1_primary, SourceTier.tier1_authority, SourceTier.tier2_established}
)
CARD_WINDOWS = (7, 30, 90)
PROVISIONAL = frozenset(
    {
        ConfirmationStatus.officially_announced_upcoming,
        ConfirmationStatus.reported,
        ConfirmationStatus.rumour_or_leak,
        ConfirmationStatus.speculative,
    }
)


@dataclass(frozen=True)
class LifecycleChange:
    signal_id: UUID
    verification_status: VerificationStatus
    status: str
    reason: str
    superseded_by: UUID | None = None
    evidence_item_id: UUID | None = None


@dataclass(frozen=True)
class SignalWithdrawal:
    """tells 07 which cards to pull and 09 which refresh insertions to roll back."""

    signal_id: UUID
    verification_status: VerificationStatus
    status: str
    reason: str
    recommendation_ids: list[str]
    refresh_keywords: list[str]


def card_ids(client_id: UUID, signal_id: UUID) -> list[str]:
    return [
        recommendation_id(make_stable_key(client_id, RecType.news_opportunity, str(signal_id), window))
        for window in CARD_WINDOWS
    ]


def _mentions(item: NewsItem, signal: NewsSignal) -> bool:
    text = f"{item.title} {item.snippet}"
    return any(contains_phrase(text, term) for term in (signal.entity, *signal.aliases))


def _after_signal(item: NewsItem, signal: NewsSignal) -> bool:
    # story members count only when they arrived after the judgement; other stories after the news broke
    if item.story_id == signal.story_id:
        return item.fetched_at > signal.created_at
    published = item.published_at or item.fetched_at
    return published >= signal.first_seen_at


class SignalLifecycle:
    """active -> disputed / retracted / superseded, plus expiry; retracted is terminal."""

    def retractions(self, signals: Sequence[NewsSignal], items: Sequence[NewsItem]) -> list[LifecycleChange]:
        changes = []
        for signal in signals:
            if signal.verification_status == "retracted":
                continue
            cues = [i for i in items if _after_signal(i, signal) and _mentions(i, signal)]
            cues = [i for i in cues if RETRACTION_CUES.search(f"{i.title} {i.snippet[:300]}")]
            if not cues:
                continue
            reliable = [i for i in cues if i.source_tier in RELIABLE_TIERS]
            if reliable:
                changes.append(
                    LifecycleChange(
                        signal.id, "retracted", "suppressed", "retracted_by_source", None, reliable[0].id
                    )
                )
            elif signal.verification_status == "active":
                changes.append(
                    LifecycleChange(
                        signal.id, "disputed", signal.status, "disputed_by_low_tier", None, cues[0].id
                    )
                )
        return changes

    def supersessions(
        self, existing: Sequence[NewsSignal], fresh: Sequence[NewsSignal]
    ) -> list[LifecycleChange]:
        changes = []
        for newer in fresh:
            if newer.verification_status != "active":
                continue
            newer_versions = version_tokens(newer.entity)
            for older in existing:
                if older.id == newer.id or older.story_id == newer.story_id:
                    continue
                if older.verification_status in {"retracted", "superseded"}:
                    continue
                if strip_versions(older.entity) != strip_versions(newer.entity):
                    continue
                older_versions = version_tokens(older.entity)
                # a confirmed story replaces an earlier announcement or report about the same name
                same_entity = (
                    older_versions == newer_versions
                    and older.first_seen_at < newer.first_seen_at
                    and older.judgement.confirmation in PROVISIONAL
                    and newer.judgement.confirmation is ConfirmationStatus.confirmed
                )
                higher = bool(
                    older_versions
                    and newer_versions
                    and max(map(version_key, newer_versions)) > max(map(version_key, older_versions))
                )
                if same_entity or higher:
                    changes.append(
                        LifecycleChange(older.id, "superseded", "suppressed", "superseded", newer.id)
                    )
        return changes

    def expiries(self, signals: Sequence[NewsSignal], now: datetime) -> list[LifecycleChange]:
        return [
            LifecycleChange(s.id, s.verification_status, "expired", "expired")
            for s in signals
            if s.expires_at <= now and s.status != "expired"
        ]

    def apply(self, signal: NewsSignal, change: LifecycleChange) -> NewsSignal:
        status = change.verification_status
        if (
            STATUS_RANK[status] < STATUS_RANK[signal.verification_status]
            and signal.verification_status != "disputed"
        ):
            status = signal.verification_status
        withdrawn = status in {"retracted", "superseded"} or change.status == "expired"
        return signal.model_copy(
            update={
                "verification_status": status,
                "superseded_by": change.superseded_by or signal.superseded_by,
                "status": change.status,
                "suppression_reason": change.reason if withdrawn else signal.suppression_reason,
                "refresh_eligible": signal.refresh_eligible and status == "active" and not withdrawn,
            }
        )

    def withdrawal(self, signal: NewsSignal, reason: str) -> SignalWithdrawal:
        return SignalWithdrawal(
            signal_id=signal.id,
            verification_status=signal.verification_status,
            status=signal.status,
            reason=reason,
            recommendation_ids=card_ids(signal.client_id, signal.id),
            refresh_keywords=list(signal.refresh_keywords),
        )
