import math
from typing import ClassVar

from app.domain.news import NewsSignal
from app.domain.refresh import ClaimLevel, TriggerEvidence
from app.services.refresh.context import RefreshContext
from app.services.refresh.eligibility import VideoEligibility, VideoState
from app.services.refresh.evidence import VideoContentEvidence
from app.services.refresh.text import KeywordPresence, is_distinctive_alias, normalise, stem
from app.services.refresh.triggers.base import Finding, TriggerKind, dedupe_key, register_detector

COVERED_NOUNS = ("workflow", "steps", "method", "process", "setup", "strategy", "tips", "guide")


def usable_aliases(signal: NewsSignal) -> tuple[str, ...]:
    entity = normalise(signal.entity)
    return tuple(
        alias for alias in signal.aliases if is_distinctive_alias(alias) and normalise(alias) != entity
    )


@register_detector
class NewsEntityDetector:
    kind: ClassVar[TriggerKind] = TriggerKind.news_entity

    def detect(
        self, state: VideoState, gate: VideoEligibility, evidence: VideoContentEvidence, ctx: RefreshContext
    ) -> list[Finding]:
        if not gate.keyword_eligible:
            return []
        video = state.video
        description = KeywordPresence(video.description)
        title = KeywordPresence(video.title)
        tags = KeywordPresence(" ".join(video.tags))
        findings = []
        for signal in ctx.news_signals:
            if not self._signal_usable(signal, ctx):
                continue
            shared = set(signal.topic_ref_ids) & evidence.covered_topic_refs
            if not shared:
                continue
            event_day = signal.event_date or signal.first_seen_at.date()
            if video.published_at.date() >= event_day:
                continue
            aliases = usable_aliases(signal)
            phrases = (signal.entity, *aliases)
            if description.contains_any(phrases):
                continue
            strength = max(evidence.topic_scores[ref_id] for ref_id in shared)
            notes = []
            if title.contains_any(phrases):
                strength *= ctx.settings.title_presence_relevance_factor
                notes.append(f"'{signal.entity}' is already in the title but not in the description")
            if tags.contains_any(phrases):
                notes.append(
                    f"'{signal.entity}' is already in your tags, which YouTube says play a minimal role. "
                    "Adding it to the description helps search."
                )
            if state.is_short:
                strength *= ctx.settings.shorts_news_weight
            days_since = max(0, (ctx.now.date() - event_day).days)
            owner_terms = ctx.owner_search_terms.get(video.video_id, ())
            demand = ctx.demand.collect(signal.entity, aliases, owner_terms)
            confirmed = normalise(signal.entity) in evidence.owner_confirmed_keywords
            level = ClaimLevel.l3_applicability if confirmed else ClaimLevel.l1_context
            if not ctx.policy.permits_claim(self.kind.value, level):
                level = ClaimLevel.l1_context
            findings.append(
                Finding(
                    kind=self.kind,
                    video_id=video.video_id,
                    dedupe_key=dedupe_key(self.kind, signal.entity),
                    strength=strength,
                    demand=demand.demand,
                    urgency=math.exp(-days_since / 7),
                    evidence=TriggerEvidence(
                        reference_video_ids=[ref.video.video_id for ref in demand.outlier_refs],
                        news_sources=list(signal.sources),
                        owner_search_terms=list(owner_terms) if demand.owner_search_hit else [],
                        notes=notes,
                    ),
                    keyword=signal.entity,
                    aliases=aliases,
                    claim_level=level,
                    params={
                        "entity": signal.entity,
                        "entity_kind": signal.entity_kind,
                        "month_year": ctx.month_year,
                        "covered_noun": next(
                            (noun for noun in COVERED_NOUNS if stem(noun) in evidence.covered_tokens),
                            "content",
                        ),
                        "first_seen_at": signal.first_seen_at.isoformat(),
                    },
                    demand_evidence=demand,
                    news_signal_id=signal.id,
                )
            )
        return findings

    @staticmethod
    def _signal_usable(signal: NewsSignal, ctx: RefreshContext) -> bool:
        return (
            signal.verification_status == "active"
            and signal.refresh_eligible
            and not signal.is_competitor_entity
            and signal.relevance_score >= ctx.decision.news_relevance_min
            and signal.expires_at > ctx.now
            and ctx.policy.allows_news(signal, ctx.client_domains)
        )
