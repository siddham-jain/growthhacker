from typing import ClassVar

from app.domain.refresh import Advisory, ClaimLevel, TriggerEvidence
from app.services.refresh.context import RefreshContext
from app.services.refresh.eligibility import VideoEligibility, VideoState
from app.services.refresh.evidence import VideoContentEvidence
from app.services.refresh.links.status import LinkStatusClass
from app.services.refresh.links.urls import normalise_url
from app.services.refresh.triggers.base import Finding, TriggerKind, dedupe_key, register_detector


@register_detector
class BrokenLinkDetector:
    """applies to any video with enough views, earning or not: a broken link is worth fixing anywhere."""

    kind: ClassVar[TriggerKind] = TriggerKind.broken_link

    def detect(
        self, state: VideoState, gate: VideoEligibility, evidence: VideoContentEvidence, ctx: RefreshContext
    ) -> list[Finding]:
        if not gate.hygiene_eligible or state.video.view_count < ctx.settings.broken_link_min_views:
            return []
        findings = []
        seen: set[str] = set()
        for url in evidence.structure.urls:
            status = ctx.link_statuses.get(url.normalised)
            if status is None or not status.broken or url.raw in seen:
                continue
            seen.add(url.raw)
            repaired = status.repaired_url
            repaired_ok = repaired is not None and self._repair_verified(repaired, ctx)
            fixable = (
                status.status_class is LinkStatusClass.malformed and repaired_ok and not state.maybe_short
            )
            advisories: tuple[Advisory, ...] = ()
            if state.maybe_short:
                advisories = (
                    Advisory(
                        kind="shorts_links_not_clickable",
                        message=(
                            f"{status.describe(url.raw)}. Links in Shorts descriptions aren't clickable, "
                            "so consider removing it."
                        ),
                        risk="low",
                    ),
                )
            elif not fixable:
                advisories = (
                    Advisory(
                        kind="manual_review",
                        message=f"{status.describe(url.raw)}. Remove or replace it in Studio.",
                        risk="medium",
                    ),
                )
            findings.append(
                Finding(
                    kind=self.kind,
                    video_id=state.video_id,
                    dedupe_key=dedupe_key(self.kind, url.normalised),
                    strength=1.0,
                    demand=0.0,
                    urgency=1.0,
                    evidence=TriggerEvidence(defect=status.describe(url.raw)),
                    claim_level=ClaimLevel.l0_navigational if fixable else None,
                    params={"old_text": url.raw, "new_text": repaired or "", "start": str(url.start)},
                    advisories=advisories,
                )
            )
        return findings

    @staticmethod
    def _repair_verified(repaired: str, ctx: RefreshContext) -> bool:
        status = ctx.link_statuses.get(normalise_url(repaired))
        return status is not None and status.status_class in (LinkStatusClass.ok, LinkStatusClass.redirect)
