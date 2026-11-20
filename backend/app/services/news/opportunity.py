from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from app.core.config import NewsSettings
from app.domain.common import ChannelRole, FormatSegment
from app.domain.confidence import Confidence, ConfidenceBasis, ConfidenceLabel, ConfidenceRuleId
from app.domain.news import EventType, NewsItem, NewsSignal, NewsSourceRef, SourceTier
from app.domain.profile import ConversionAction
from app.domain.recommendations import (
    EvidenceNumber,
    EvidenceSource,
    ExpectedRange,
    FormatSuggestion,
    Lens,
    MatchSpec,
    NewsCompliance,
    NewsSubject,
    Reason,
    RecommendationDraft,
    RecType,
    ReferenceVideo,
    make_stable_key,
    recommendation_id,
)
from app.services.news.context import NewsClientContext
from app.services.news.demand import CandidateVideo
from app.services.news.normalize import effective_published_at
from app.services.news.tiers import TIER_RANK

PRODUCER = "news_radar@1"
MAX_REFERENCE_SOURCES = 5
MAX_REFERENCES = 5
MAX_POOL = 12
SHORT_EVENTS = frozenset(
    {
        EventType.model_release,
        EventType.product_launch,
        EventType.pricing_change,
        EventType.public_health_advisory,
    }
)
EXPLAINER_EVENTS = frozenset(
    {
        EventType.clinical_guideline,
        EventType.regulatory_approval,
        EventType.policy_change,
        EventType.research_publication,
        EventType.outbreak,
    }
)
HEALTH_CTA_TYPES = ("book_appointment", "call", "whatsapp", "contact_form", "visit_location")
SURFACE_RECOMMENDATIONS = "surface:recommendations"
SURFACE_EARLY_SIGNALS = "surface:early_signals"
SURFACE_REVIEW = "surface:review"


@dataclass(frozen=True)
class OpportunityInputs:
    signal: NewsSignal
    items: Sequence[NewsItem]
    videos: Mapping[str, CandidateVideo]
    expected_range: ExpectedRange
    demo_event_types: frozenset[EventType]
    health: bool = False
    shorts_beat_long_form: bool = False


def reference_sources(items: Sequence[NewsItem]) -> list[NewsSourceRef]:
    ranked = sorted(
        items,
        key=lambda i: (-TIER_RANK[i.source_tier], "paywalled" in i.flags, "social" in i.flags, i.url_hash),
    )
    picked: list[NewsSourceRef] = []
    seen: set[str] = set()
    for item in ranked:
        if item.domain in seen or "social" in item.flags:
            continue
        seen.add(item.domain)
        picked.append(
            NewsSourceRef(
                url=item.url,
                publisher=item.domain,
                published_at=item.published_at,
                tier=item.source_tier,
                title=item.title,
            )
        )
    return picked[:MAX_REFERENCE_SOURCES]


def pick_conversion_action(signal: NewsSignal, ctx: NewsClientContext) -> ConversionAction | None:
    actions = {a.id: a for a in ctx.profile.conversion_actions}
    if ctx.is_health:
        safe = [a for a in ctx.profile.conversion_actions if a.type in HEALTH_CTA_TYPES]
        actions = {a.id: a for a in safe}
    judged = signal.judgement.conversion_action_id
    if judged and judged in actions:
        return actions[judged]
    offering_ids = {link.offering_id for link in signal.judgement.offering_links}
    for action in actions.values():
        if offering_ids & set(action.offering_ids):
            return action
    return next((a for a in actions.values() if a.is_primary), next(iter(actions.values()), None))


def format_suggestion(inputs: OpportunityInputs) -> FormatSuggestion:
    signal = inputs.signal
    event = signal.judgement.event_type
    saturation = signal.demand.saturation
    demoable = bool(signal.judgement.offering_links) and event in inputs.demo_event_types
    if saturation == "crowded" and demoable:
        return FormatSuggestion(format="tutorial", rationale_codes=["CROWDED_DIFFERENTIATE_WITH_PRODUCT"])
    if event in EXPLAINER_EVENTS:
        return FormatSuggestion(format="long_explainer", rationale_codes=["EXPLAINER_EVENT"])
    if inputs.health and event is EventType.public_health_advisory:
        return FormatSuggestion(format="long_explainer", rationale_codes=["HEALTH_CAREFUL_FRAMING"])
    if (signal.urgency == "now" and event in SHORT_EVENTS) or inputs.shorts_beat_long_form:
        codes = ["URGENT_LAUNCH"] if signal.urgency == "now" else ["OWN_SHORTS_OUTPERFORM"]
        if demoable:
            codes.append("TUTORIAL_FOLLOW_UP")
        return FormatSuggestion(format="short", rationale_codes=codes)
    if demoable:
        return FormatSuggestion(format="tutorial", rationale_codes=["DEMO_IN_CLIENT_PRODUCT"])
    return FormatSuggestion(format="long_explainer", rationale_codes=["DEFAULT_EXPLAINER"])


def news_confidence(signal: NewsSignal, min_refs: int) -> Confidence:
    demand = signal.demand
    outliers = [v for v in demand.early_videos if v.is_outlier]
    outlier_channels = len({v.channel_id for v in outliers})
    if demand.early_video_count < min_refs:
        return Confidence(
            label=ConfidenceLabel.low,
            outliers=len(outliers),
            channels=outlier_channels,
            basis=ConfidenceBasis.first_mover,
            matching_videos=demand.early_video_count,
            rule_id=ConfidenceRuleId.news_v1,
        )
    if len(outliers) >= 5 and outlier_channels >= 3:
        label = ConfidenceLabel.high
    elif len(outliers) >= 2:
        label = ConfidenceLabel.medium
    else:
        label = ConfidenceLabel.low
    caps = []
    if signal.judgement.confirmation.value in {"rumour_or_leak", "speculative"}:
        label, caps = ConfidenceLabel.low, ["UNCONFIRMED_CAP"]
    return Confidence(
        label=label,
        outliers=len(outliers),
        channels=outlier_channels,
        basis=ConfidenceBasis.standard,
        matching_videos=demand.early_video_count,
        rule_id=ConfidenceRuleId.news_v1,
        caps_applied=caps,
    )


def _hours_ago(item: NewsItem, now: datetime) -> str:
    published = effective_published_at(item)
    if published is None:
        return "date unknown"
    if item.published_precision == "date":
        return f"published {item.published_at:%b %d}"
    return f"{max(int((now - published).total_seconds() // 3600), 0)}h ago"


class NewsOpportunityBuilder:
    """turns one emitted NewsSignal into a news_opportunity RecommendationDraft with typed evidence."""

    def __init__(self, settings: NewsSettings) -> None:
        self._settings = settings

    def _references(self, inputs: OpportunityInputs) -> list[ReferenceVideo]:
        early = sorted(
            inputs.signal.demand.early_videos,
            key=lambda v: (v.is_outlier, v.outlier_ratio or 0.0, v.views),
            reverse=True,
        )
        references = []
        for video in early[:MAX_POOL]:
            candidate = inputs.videos.get(video.video_id)
            if candidate is None:
                continue
            age = video.views_at_age
            references.append(
                ReferenceVideo(
                    video_id=video.video_id,
                    url=f"https://www.youtube.com/watch?v={video.video_id}",
                    title=candidate.title,
                    channel=video.channel_title,
                    channel_id=video.channel_id,
                    channel_role=ChannelRole.niche,
                    published_at=video.published_at,
                    views_day7=age.views if age.age_days == 7 else None,
                    views_at_age=age,
                    outlier_ratio=video.outlier_ratio,
                    is_outlier=video.is_outlier,
                    length_band=video.duration_band,
                    thumbnail_url=candidate.thumbnail_url,
                )
            )
        return references

    def _reasons(self, inputs: OpportunityInputs, now: datetime) -> list[Reason]:
        signal = inputs.signal
        credibility, _demand = signal.credibility, signal.demand
        reasons: list[Reason] = []
        primary = next((i for i in inputs.items if i.source_tier is SourceTier.tier1_primary), None)
        authority = next((i for i in inputs.items if i.source_tier is SourceTier.tier1_authority), None)
        if primary or authority:
            source = primary or authority
            code = "PRIMARY_SOURCE" if primary else "AUTHORITY_SOURCE"
            reasons.append(
                Reason(
                    reason_id="news_source",
                    code=code,
                    params={"domain": source.domain, "age": _hours_ago(source, now)},
                    statement=f"Announced by {source.domain} ({_hours_ago(source, now)})",
                    source=EvidenceSource.news,
                )
            )
        outlets = credibility.independent_outlets
        reasons.append(
            Reason(
                reason_id="news_outlets",
                code="OUTLETS",
                params={"outlets": outlets},
                statement=f"Covered by {outlets} independent outlet{'s' if outlets != 1 else ''}",
                numbers=[EvidenceNumber(value=outlets, unit="count", display=str(outlets))],
                source=EvidenceSource.news,
            )
        )
        reasons.append(self._demand_reason(signal))
        if signal.judgement.confirmation.value in {"rumour_or_leak", "speculative", "reported"}:
            reasons.append(
                Reason(
                    reason_id="news_unconfirmed",
                    code="UNCONFIRMED_REPORT",
                    params={"confirmation": signal.judgement.confirmation.value},
                    statement="Unconfirmed report; not yet confirmed by the organisation",
                    source=EvidenceSource.news,
                )
            )
        if "PREPRINT_ONLY" in credibility.reasons:
            reasons.append(
                Reason(
                    reason_id="news_preprint",
                    code="PREPRINT",
                    statement="Preprint; not peer reviewed",
                    source=EvidenceSource.news,
                )
            )
        return reasons

    def _demand_reason(self, signal: NewsSignal) -> Reason:
        demand = signal.demand
        if demand.status in {"unchecked_quota", "unchecked_error", "unchecked_disabled"}:
            statement = (
                "YouTube demand not checked today (quota)"
                if demand.status == "unchecked_quota"
                else "YouTube demand not checked"
            )
            return Reason(
                reason_id="news_demand",
                code="DEMAND_UNCHECKED",
                params={"status": demand.status},
                statement=statement,
                source=EvidenceSource.youtube_public,
            )
        ids = [v.video_id for v in demand.early_videos][:MAX_REFERENCES]
        numbers = [
            EvidenceNumber(
                value=demand.early_video_count, unit="videos", display=str(demand.early_video_count)
            ),
            EvidenceNumber(value=demand.outlier_count, unit="count", display=str(demand.outlier_count)),
        ]
        if demand.early_video_count < self._settings.min_reference_videos:
            statement = "Few or no videos yet; evidence is the news itself"
            if demand.early_video_count:
                statement = (
                    f"Only {demand.early_video_count} early video{'s' if demand.early_video_count != 1 else ''} "
                    f"so far ({demand.outlier_count} above their channel's normal); evidence is the news itself"
                )
            return Reason(
                reason_id="news_demand",
                code="FIRST_MOVER",
                params={"videos": demand.early_video_count, "outliers": demand.outlier_count},
                statement=statement,
                numbers=numbers,
                ref_video_ids=ids,
                source=EvidenceSource.youtube_public,
            )
        median = demand.median_views_per_hour or 0.0
        numbers.append(EvidenceNumber(value=round(median), unit="views", display=f"{median:,.0f}"))
        return Reason(
            reason_id="news_demand",
            code="EARLY_DEMAND",
            params={
                "videos": demand.early_video_count,
                "outliers": demand.outlier_count,
                "median_vph": round(median),
            },
            statement=(
                f"{demand.early_video_count} early videos, {demand.outlier_count} above their channel's normal, "
                f"median {median:,.0f} views/hour"
            ),
            numbers=numbers,
            ref_video_ids=ids,
            source=EvidenceSource.youtube_public,
        )

    def _fit_reasons(
        self, inputs: OpportunityInputs, ctx: NewsClientContext, action: ConversionAction | None
    ) -> list[Reason]:
        signal = inputs.signal
        offerings = {o.id: o for o in ctx.profile.offerings}
        value_props = {v.id: v for v in ctx.profile.value_propositions}
        questions = {q.id: q for q in ctx.buyer_questions}
        reasons = []
        for index, link in enumerate(signal.judgement.offering_links):
            offering = offerings[link.offering_id]
            reasons.append(
                Reason(
                    reason_id=f"fit_offering_{index}",
                    code="OFFERING_LINK",
                    params={"offering_id": offering.id, "offering": offering.name},
                    statement=f"Ties to your '{offering.name}' offering",
                    source=EvidenceSource.website,
                )
            )
            for vp_id in link.value_prop_ids:
                reasons.append(
                    Reason(
                        reason_id=f"fit_value_prop_{vp_id}",
                        code="VALUE_PROP_LINK",
                        params={"value_prop_id": vp_id},
                        statement=f"Supports your claim: {value_props[vp_id].statement}"[:200],
                        source=EvidenceSource.website,
                    )
                )
        for question_id in signal.judgement.buyer_question_ids:
            if question_id in questions:
                reasons.append(
                    Reason(
                        reason_id=f"fit_question_{question_id.hex[:8]}",
                        code="BUYER_QUESTION",
                        params={"question": questions[question_id].text[:120]},
                        statement=f"Answers: {questions[question_id].text}"[:200],
                        source=EvidenceSource.client_config,
                    )
                )
        if action is not None:
            reasons.append(
                Reason(
                    reason_id="fit_cta",
                    code="CTA",
                    params={"action_id": action.id, "label": action.label},
                    statement=f"CTA: {action.label}",
                    source=EvidenceSource.website,
                )
            )
        history = inputs.expected_range
        if history.basis == "own":
            reasons.append(
                Reason(
                    reason_id="fit_history",
                    code="OWN_NEWS_HISTORY",
                    params={"n": history.n_own},
                    statement=history.display[:200],
                    numbers=[EvidenceNumber(value=history.n_own, unit="videos", display=str(history.n_own))],
                    source=EvidenceSource.youtube_public,
                )
            )
        return reasons

    def build(self, inputs: OpportunityInputs, ctx: NewsClientContext, now: datetime) -> RecommendationDraft:
        signal = inputs.signal
        action = pick_conversion_action(signal, ctx)
        references = self._references(inputs)
        confidence = news_confidence(signal, self._settings.min_reference_videos)
        first_mover = confidence.basis is ConfidenceBasis.first_mover
        review = (
            ctx.is_health
            and self._settings.health_review_required
            or signal.safety.decision == "needs_review"
        )
        if review:
            surface = SURFACE_REVIEW
        elif first_mover:
            surface = SURFACE_EARLY_SIGNALS
        else:
            surface = SURFACE_RECOMMENDATIONS
        stable_key = make_stable_key(ctx.client_id, RecType.news_opportunity, str(signal.id), ctx.window_days)
        subject = NewsSubject(
            news_signal_id=signal.id,
            story_id=signal.story_id,
            key_entity=signal.entity,
            angle=signal.judgement.angle,
            urgency=signal.urgency,
            expires_at=signal.expires_at,
            reference_sources=reference_sources(inputs.items),
            format_suggestion=format_suggestion(inputs),
            conversion_action_id=action.id if action else None,
            compliance=NewsCompliance(
                disclaimers=list(signal.safety.disclaimers),
                review_required=review,
                safety_flags=list(signal.judgement.safety_flags),
            ),
        )
        lenses = [Lens.conversion] if signal.judgement.buyer_question_ids or action else []
        if signal.demand.status in {"checked", "local_only"} and signal.demand.saturation in {
            "emerging",
            "active",
        }:
            lenses.insert(0, Lens.views)
        flags = [surface, f"urgency:{signal.urgency}"]
        flags += ["first_mover"] if first_mover else []
        flags += ["review_required"] if review else []
        flags += ["health"] if ctx.is_health else []
        flags += [f"safety:{reason}" for reason in signal.safety.reasons]
        dates = [d for d in (signal.first_seen_at.date(), signal.event_date) if d is not None]
        return RecommendationDraft(
            draft_id=recommendation_id(stable_key),
            stable_key=stable_key,
            client_id=ctx.client_id,
            type=RecType.news_opportunity,
            window_days=ctx.window_days,
            as_of=now,
            produced_by=PRODUCER,
            subject=subject,
            topic_ids=list(signal.topic_ref_ids),
            buyer_question_id=signal.judgement.buyer_question_ids[0]
            if signal.judgement.buyer_question_ids
            else None,
            related_buyer_question_ids=list(signal.judgement.buyer_question_ids[1:]),
            reference_pool=references,
            references=references[:MAX_REFERENCES],
            reasons=self._reasons(inputs, now),
            fit_reasons=self._fit_reasons(inputs, ctx, action),
            expected_range=inputs.expected_range,
            confidence=confidence,
            match_spec=MatchSpec(
                spec_id=f"news:{signal.id}",
                rec_type=RecType.news_opportunity,
                topic_ids=list(signal.topic_ref_ids),
                format=FormatSegment.short_form
                if subject.format_suggestion.format == "short"
                else FormatSegment.long_form,
                lens=lenses or [Lens.views],
                buyer_question_id=signal.judgement.buyer_question_ids[0]
                if signal.judgement.buyer_question_ids
                else None,
                specificity=1 + len(signal.judgement.offering_links),
                source="system",
                source_ref=str(signal.id),
            ),
            forbidden_titles=[r.title for r in references],
            allowed_dates=dates,
            entities=list(dict.fromkeys([signal.entity, *signal.aliases])),
            flags=flags,
        )
