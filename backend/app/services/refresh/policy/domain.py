from collections.abc import Collection
from typing import ClassVar, Protocol

import regex

from app.core.registry import Registry
from app.domain.news import NewsSignal, SafetyFlag, SourceTier
from app.domain.profile import FreshnessProfile
from app.domain.refresh import ClaimLevel
from app.services.refresh.links.urls import on_domains
from app.services.refresh.policy.lexicons import (
    CREDENTIAL_TERMS,
    DMR_SCHEDULE_CONDITIONS,
    GENERIC_CLAIM_TERMS,
    HEALTHCARE_CLAIM_TERMS,
    HEALTHCARE_EFFICACY_VERBS,
    HEALTHCARE_SUPERLATIVES,
)
from app.services.refresh.text import KeywordPresence

DOCTOR_NAME = regex.compile(r"\bDr\.?\s+\p{Lu}[\p{L}.]*")
AUTHORITATIVE_TIERS = frozenset({SourceTier.tier1_authority, SourceTier.tier1_primary})


def _phrase_hits(text: str, phrases: Collection[str]) -> list[str]:
    presence = KeywordPresence(text)
    lowered = text.casefold()
    return [
        phrase for phrase in phrases if (phrase in lowered if "%" in phrase else presence.contains(phrase))
    ]


class DomainPolicy(Protocol):
    domain_kind: ClassVar[str]
    review_required: ClassVar[bool]

    def permits_claim(self, trigger_kind: str, level: ClaimLevel) -> bool: ...

    def allows_news(self, signal: NewsSignal, client_domains: frozenset[str]) -> bool: ...

    def violations(self, text: str, description: str) -> list[str]: ...

    def wants_disclaimer(self) -> bool: ...


domain_policy_registry: Registry[type[DomainPolicy]] = Registry("refresh_domain_policy_registry")


@domain_policy_registry.register("none")
class DefaultPolicy:
    domain_kind: ClassVar[str] = "none"
    review_required: ClassVar[bool] = False

    def permits_claim(self, trigger_kind: str, level: ClaimLevel) -> bool:
        return True

    def allows_news(self, signal: NewsSignal, client_domains: frozenset[str]) -> bool:
        return signal.safety.decision in ("allow", "allow_with_guidance")

    def violations(self, text: str, description: str) -> list[str]:
        return [f"claim term '{term}'" for term in _phrase_hits(text, GENERIC_CLAIM_TERMS)]

    def wants_disclaimer(self) -> bool:
        return False


@domain_policy_registry.register("healthcare")
class HealthcarePolicy(DefaultPolicy):
    """india healthcare pack: no medical claims, no new names or credentials, news only for non-clinical events."""

    domain_kind: ClassVar[str] = "healthcare"
    review_required: ClassVar[bool] = True
    news_entity_kinds: ClassVar[frozenset[str]] = frozenset({"policy", "org", "other"})
    blocked_safety_flags: ClassVar[frozenset[SafetyFlag]] = frozenset(
        {SafetyFlag.medical_claim, SafetyFlag.unverified_health_claim, SafetyFlag.death, SafetyFlag.tragedy}
    )

    def permits_claim(self, trigger_kind: str, level: ClaimLevel) -> bool:
        if level is ClaimLevel.l3_applicability:
            return False
        if trigger_kind == "news_entity":
            return level is ClaimLevel.l1_context
        if trigger_kind in ("above_fold_keyword", "buyer_question_gap"):
            return level is ClaimLevel.l2_coverage
        return True

    def allows_news(self, signal: NewsSignal, client_domains: frozenset[str]) -> bool:
        if signal.safety.decision not in ("allow", "allow_with_guidance"):
            return False
        if signal.entity_kind not in self.news_entity_kinds:
            return False
        if self.blocked_safety_flags & set(signal.judgement.safety_flags):
            return False
        official = signal.credibility.has_authoritative_health_source or any(
            source.tier in AUTHORITATIVE_TIERS for source in signal.sources
        )
        own_site = any(on_domains(source.url, client_domains) for source in signal.sources)
        return official or own_site

    def violations(self, text: str, description: str) -> list[str]:
        problems = super().violations(text, description)
        problems += [f"medical claim term '{term}'" for term in _phrase_hits(text, HEALTHCARE_CLAIM_TERMS)]
        existing = KeywordPresence(description)
        problems += [
            f"superlative '{term}' not already in your description"
            for term in _phrase_hits(text, HEALTHCARE_SUPERLATIVES)
            if not existing.contains(term)
        ]
        conditions = _phrase_hits(text, DMR_SCHEDULE_CONDITIONS)
        verbs = _phrase_hits(text, HEALTHCARE_EFFICACY_VERBS)
        if conditions and verbs:
            problems.append(f"'{verbs[0]}' combined with '{conditions[0]}' (scheduled condition)")
        problems += [
            f"doctor name '{name}' not already in your description"
            for name in DOCTOR_NAME.findall(text)
            if name.casefold() not in description.casefold()
        ]
        problems += [
            f"credential '{term}' not already in your description"
            for term in _phrase_hits(text, CREDENTIAL_TERMS)
            if not existing.contains(term)
        ]
        return problems

    def wants_disclaimer(self) -> bool:
        return True


class RegulatedFallbackPolicy(DefaultPolicy):
    # regulated niches without a dedicated pack still go through human review
    review_required: ClassVar[bool] = True


def policy_for(freshness: FreshnessProfile) -> DomainPolicy:
    kind = freshness.domain_kind if freshness.regulated_domain else "none"
    if kind in domain_policy_registry:
        return domain_policy_registry.get(kind)()
    return RegulatedFallbackPolicy() if freshness.regulated_domain else DefaultPolicy()
