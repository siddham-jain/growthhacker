import hashlib
import re
import unicodedata
from collections.abc import Sequence
from datetime import datetime, timedelta

from app.domain.profile import ClaimMeta, ClaimOrigin, ClaimStatus, Evidence
from app.domain.web import CONFIRMATION_REQUIRED_SOURCES, ExtractionKind, SourceKind

INFERRED_CONFIDENCE_CAP = 0.6
THIRD_PARTY_CONFIDENCE_CAP = 0.6
_NON_WORD = re.compile(r"[^\w]+", re.UNICODE)


def normalise_name(text: str) -> str:
    return " ".join(_NON_WORD.sub(" ", unicodedata.normalize("NFKC", text).casefold()).split())


def stable_id(prefix: str, *parts: str | None) -> str:
    digest = hashlib.sha1("|".join(normalise_name(part or "") for part in parts).encode()).hexdigest()[:12]
    return f"{prefix}_{digest}"


def confidence(evidence: Sequence[Evidence], *, home_urls: frozenset[str] = frozenset()) -> float:
    """min(1, 0.5 + 0.15·(pages−1) + 0.15·json_ld + 0.1·nav + 0.1·home) per plan 03 §5.11.8."""
    pages = {item.source_url for item in evidence}
    score = 0.5 + 0.15 * (len(pages) - 1)
    score += 0.15 if any(item.extraction is ExtractionKind.json_ld for item in evidence) else 0.0
    score += 0.1 if any(item.extraction is ExtractionKind.nav for item in evidence) else 0.0
    score += 0.1 if pages & home_urls else 0.0
    if evidence and all(item.source_kind in CONFIRMATION_REQUIRED_SOURCES for item in evidence):
        score = min(score, THIRD_PARTY_CONFIDENCE_CAP)
    return round(min(1.0, score), 3)


class ClaimFactory:
    """builds ClaimMeta with the status rules: user → confirmed; third-party or archive → needs confirmation."""

    def __init__(
        self, *, version: int, now: datetime, home_urls: frozenset[str], stale_after: timedelta
    ) -> None:
        self.version = version
        self.now = now
        self.home_urls = home_urls
        self.stale_after = stale_after

    def _status(self, evidence: Sequence[Evidence], origin: ClaimOrigin) -> ClaimStatus:
        if origin is ClaimOrigin.user or (
            evidence and all(item.source_kind is SourceKind.user for item in evidence)
        ):
            return ClaimStatus.confirmed
        if evidence and all(
            item.snapshot_at is not None and self.now - item.snapshot_at > self.stale_after
            for item in evidence
        ):
            return ClaimStatus.stale
        if evidence and all(item.source_kind in CONFIRMATION_REQUIRED_SOURCES for item in evidence):
            return ClaimStatus.needs_confirmation
        return ClaimStatus.proposed

    def evidenced(
        self, evidence: Sequence[Evidence], *, origin: ClaimOrigin = ClaimOrigin.extracted
    ) -> ClaimMeta:
        status = self._status(evidence, origin)
        return ClaimMeta(
            origin=ClaimOrigin.user
            if status is ClaimStatus.confirmed and origin is not ClaimOrigin.user
            else origin,
            status=status,
            confidence=1.0
            if status is ClaimStatus.confirmed
            else confidence(evidence, home_urls=self.home_urls),
            evidence=list(evidence)[:5],
            locked=status is ClaimStatus.confirmed,
            first_seen_version=self.version,
            updated_at=self.now,
        )

    def based(
        self, basis_ids: Sequence[str], *, origin: ClaimOrigin = ClaimOrigin.derived, score: float = 0.5
    ) -> ClaimMeta:
        return ClaimMeta(
            origin=origin,
            status=ClaimStatus.proposed,
            confidence=min(score, INFERRED_CONFIDENCE_CAP),
            basis_ids=list(dict.fromkeys(basis_ids)) or ["profile"],
            first_seen_version=self.version,
            updated_at=self.now,
        )
