from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import ClassVar, Literal, Protocol

from app.core.registry import Registry
from app.domain.news import (
    CredibilityAssessment,
    EventType,
    NewsItem,
    NewsJudgement,
    SafetyDecision,
    SafetyFlag,
    SourceTier,
)
from app.services.news.context import NewsClientContext
from app.services.news.text import contains_phrase

Decision = Literal["allow", "allow_with_guidance", "needs_review", "block"]
SEVERITY: dict[Decision, int] = {"allow": 0, "allow_with_guidance": 1, "needs_review": 2, "block": 3}
HEALTH_DISCLAIMER = "Educational information, not medical advice. Consult a doctor about your situation."
SEEK_CARE_GUIDANCE = "Frame around symptoms and when to seek care; cite the health authority."
HARM_FLAGS = frozenset({SafetyFlag.tragedy, SafetyFlag.death, SafetyFlag.self_harm, SafetyFlag.minors})
REVIEW_FLAGS = frozenset({SafetyFlag.legal, SafetyFlag.controversy})
CLAIM_FLAGS = frozenset({SafetyFlag.medical_claim, SafetyFlag.unverified_health_claim})
ADVISORY_EVENTS = frozenset({EventType.public_health_advisory, EventType.outbreak})
NEGATIVE_EVENTS = frozenset({EventType.incident, EventType.recall, EventType.regulatory_action})
EMBARGO_MARKERS = ("under embargo", "embargoed until", "embargo until", "embargo lifts")


@dataclass(frozen=True)
class SafetyInput:
    judgement: NewsJudgement
    credibility: CredibilityAssessment
    items: Sequence[NewsItem]
    ctx: NewsClientContext
    alarm_words: Sequence[str] = ()


@dataclass(frozen=True)
class SafetyVerdict:
    decision: Decision
    reasons: list[str] = field(default_factory=list)
    disclaimers: list[str] = field(default_factory=list)


ALLOW = SafetyVerdict("allow")


class SafetyPolicy(Protocol):
    name: ClassVar[str]

    def applies(self, ctx: NewsClientContext) -> bool: ...

    def evaluate(self, data: SafetyInput) -> SafetyVerdict: ...


safety_policy_registry: Registry[type[SafetyPolicy]] = Registry("news_safety_policy_registry")


def _text(items: Sequence[NewsItem]) -> str:
    return " ".join(f"{item.title} {item.snippet}" for item in items)


@safety_policy_registry.register("brand")
class BrandSafetyPolicy:
    name: ClassVar[str] = "brand"

    def applies(self, ctx: NewsClientContext) -> bool:
        return True

    def evaluate(self, data: SafetyInput) -> SafetyVerdict:
        flags = set(data.judgement.safety_flags)
        if flags & HARM_FLAGS:
            authority_advisory = (
                data.judgement.event_type in ADVISORY_EVENTS
                and data.credibility.best_tier is SourceTier.tier1_authority
            )
            if authority_advisory and not flags & {SafetyFlag.self_harm, SafetyFlag.minors}:
                return SafetyVerdict("allow_with_guidance", ["GUIDANCE_NO_SENSATIONAL_FRAMING"])
            return SafetyVerdict("block", ["BRAND_HARM_TOPIC"])
        if SafetyFlag.political in flags:
            if data.judgement.event_type is EventType.policy_change:
                return SafetyVerdict("allow_with_guidance", ["GUIDANCE_NEUTRAL_EXPLAINER"])
            return SafetyVerdict("needs_review", ["POLITICAL"])
        if flags & REVIEW_FLAGS:
            return SafetyVerdict("needs_review", ["LEGAL_OR_CONTROVERSY"])
        return ALLOW


@safety_policy_registry.register("healthcare")
class HealthcarePolicy:
    name: ClassVar[str] = "healthcare"

    def applies(self, ctx: NewsClientContext) -> bool:
        return ctx.is_health

    def evaluate(self, data: SafetyInput) -> SafetyVerdict:
        judgement, credibility = data.judgement, data.credibility
        flags = set(judgement.safety_flags)
        if flags & CLAIM_FLAGS and not credibility.has_authoritative_health_source:
            return SafetyVerdict("block", ["HEALTH_CLAIM_WITHOUT_AUTHORITY"])
        reasons = ["HEALTH_GUIDANCE_SEEK_CARE"]
        disclaimers = [HEALTH_DISCLAIMER, SEEK_CARE_GUIDANCE]
        if judgement.key_entity.kind == "drug" and judgement.event_type in {
            EventType.regulatory_approval,
            EventType.product_launch,
        }:
            reasons.append("HEALTH_NO_DRUG_PROMOTION")
        if flags & CLAIM_FLAGS and "IN" in data.ctx.regions:
            reasons.append("REVIEW_DRUGS_AND_MAGIC_REMEDIES_ACT")
        text = _text(data.items)
        if SafetyFlag.fear_inducing in flags or any(contains_phrase(text, w) for w in data.alarm_words):
            reasons.append("GUIDANCE_PREVENTION_FACTS_ONLY")
        if judgement.region_relevance == "other_region_only":
            reasons.append("HEALTH_NOT_APPROVED_IN_REGION")
        return SafetyVerdict("allow_with_guidance", reasons, disclaimers)


@safety_policy_registry.register("competitor")
class CompetitorPolicy:
    name: ClassVar[str] = "competitor"

    def applies(self, ctx: NewsClientContext) -> bool:
        return True

    def evaluate(self, data: SafetyInput) -> SafetyVerdict:
        judgement = data.judgement
        if not judgement.about_competitor_ids:
            return ALLOW
        negative = judgement.event_type in NEGATIVE_EVENTS or set(judgement.safety_flags) & REVIEW_FLAGS
        if negative:
            return SafetyVerdict("needs_review", ["COMPETITOR_NEGATIVE_NEWS"])
        return SafetyVerdict("allow_with_guidance", ["GUIDANCE_FACTUAL_COMPARISON_ONLY"])


@safety_policy_registry.register("client")
class ClientPolicy:
    name: ClassVar[str] = "client"

    def applies(self, ctx: NewsClientContext) -> bool:
        return True

    def evaluate(self, data: SafetyInput) -> SafetyVerdict:
        judgement = data.judgement
        if not judgement.about_client:
            return ALLOW
        negative = judgement.event_type in NEGATIVE_EVENTS or set(judgement.safety_flags) & REVIEW_FLAGS
        if negative:
            return SafetyVerdict("needs_review", ["CLIENT_NEGATIVE_PRESS"])
        return SafetyVerdict("allow", ["CLIENT_AMPLIFY"])


@safety_policy_registry.register("excluded_topics")
class ExcludedTopicsPolicy:
    name: ClassVar[str] = "excluded_topics"

    def applies(self, ctx: NewsClientContext) -> bool:
        return bool(ctx.freshness.excluded_topics)

    def evaluate(self, data: SafetyInput) -> SafetyVerdict:
        text = f"{_text(data.items)} {data.judgement.key_entity.text}"
        if any(contains_phrase(text, topic) for topic in data.ctx.freshness.excluded_topics):
            return SafetyVerdict("block", ["EXCLUDED_TOPIC"])
        return ALLOW


@safety_policy_registry.register("embargo")
class EmbargoPolicy:
    name: ClassVar[str] = "embargo"

    def applies(self, ctx: NewsClientContext) -> bool:
        return True

    def evaluate(self, data: SafetyInput) -> SafetyVerdict:
        text = _text(data.items).casefold()
        if any(marker in text for marker in EMBARGO_MARKERS):
            return SafetyVerdict("block", ["EMBARGOED"])
        return ALLOW


class SafetyScreen:
    """runs every applicable policy; the most restrictive decision wins and guidance accumulates."""

    def __init__(self, policies: Sequence[SafetyPolicy] | None = None) -> None:
        self._policies = (
            list(policies) if policies is not None else [p() for p in safety_policy_registry.all().values()]
        )

    def evaluate(self, data: SafetyInput) -> SafetyDecision:
        verdicts = [policy.evaluate(data) for policy in self._policies if policy.applies(data.ctx)]
        decision: Decision = max((v.decision for v in verdicts), key=SEVERITY.__getitem__, default="allow")
        reasons = list(dict.fromkeys(reason for v in verdicts for reason in v.reasons))
        disclaimers = list(dict.fromkeys(text for v in verdicts for text in v.disclaimers))
        return SafetyDecision(decision=decision, reasons=reasons, disclaimers=disclaimers)
