import re
import uuid
from collections.abc import Sequence
from uuid import UUID

from rapidfuzz import fuzz

from app.domain.profile import (
    BusinessProfile,
    BuyerQuestion,
    BuyerQuestionIntent,
    BuyerQuestionSourceKind,
    ClaimMeta,
    ClaimOrigin,
    ClaimStatus,
    FunnelStage,
    ReviewStatus,
)
from app.ports.buyer_questions import BuyerQuestionCandidate, BuyerQuestionSource
from app.services.website_intel.profile.claims import ClaimFactory

DUPLICATE_MATCH = 90
PRETICK_COUNT = 15
NEAR_ME = re.compile(r"\bnear me\b", re.IGNORECASE)
_PUNCTUATION = re.compile(r"[^\w\s]", re.UNICODE)
INTENT_RULES: tuple[tuple[re.Pattern[str], BuyerQuestionIntent, FunnelStage], ...] = (
    (
        re.compile(r"\b(cost|price|fees?|charges?|kitna|kitne|how much|₹|rs)\b", re.I),
        BuyerQuestionIntent.cost,
        FunnelStage.decision,
    ),
    (
        re.compile(r"\b(vs|versus|better|difference|compare)\b", re.I),
        BuyerQuestionIntent.comparison,
        FunnelStage.consideration,
    ),
    (
        re.compile(r"\b(eligible|eligibility|can i|who can|candidate)\b", re.I),
        BuyerQuestionIntent.eligibility,
        FunnelStage.consideration,
    ),
    (
        re.compile(r"\b(book|appointment|schedule|timings?|open|visit hours)\b", re.I),
        BuyerQuestionIntent.booking_logistics,
        FunnelStage.decision,
    ),
    (
        re.compile(r"\b(recover|recovery|after surgery|aftercare|heal)\b", re.I),
        BuyerQuestionIntent.recovery,
        FunnelStage.post_purchase,
    ),
    (
        re.compile(r"\b(risk|side effects?|safe|success rate|complication)\b", re.I),
        BuyerQuestionIntent.outcome_risk,
        FunnelStage.consideration,
    ),
    (
        re.compile(r"\b(where|location|address|nearest|parking)\b", re.I),
        BuyerQuestionIntent.location_access,
        FunnelStage.decision,
    ),
    (
        re.compile(r"\b(accredit|certified|experience|qualified|best)\b", re.I),
        BuyerQuestionIntent.trust_credentials,
        FunnelStage.consideration,
    ),
    (
        re.compile(r"\b(how (do|does|is)|procedure|process|work)\b", re.I),
        BuyerQuestionIntent.procedure_how,
        FunnelStage.awareness,
    ),
    (
        re.compile(r"\b(not working|error|problem|issue|fix)\b", re.I),
        BuyerQuestionIntent.troubleshooting,
        FunnelStage.post_purchase,
    ),
)


def normalise_question(text: str, cities: Sequence[str]) -> tuple[str, str | None]:
    """lowercase, no punctuation, with 'near me' and own-city mentions lifted into geo_modifier."""
    geo: str | None = None
    lowered = text.casefold()
    if NEAR_ME.search(lowered):
        geo = "near me"
        lowered = NEAR_ME.sub(" ", lowered)
    for city in cities:
        pattern = re.compile(rf"\b(in|at|near)?\s*{re.escape(city.casefold())}\b")
        if pattern.search(lowered):
            geo = geo or city
            lowered = pattern.sub(" ", lowered)
    return " ".join(_PUNCTUATION.sub(" ", lowered).split()), geo


def classify_intent(text: str) -> tuple[BuyerQuestionIntent, FunnelStage]:
    for pattern, intent, stage in INTENT_RULES:
        if pattern.search(text):
            return intent, stage
    return BuyerQuestionIntent.other, FunnelStage.awareness


class BuyerQuestionService:
    """merges every BuyerQuestionSource by precedence and dedupes fuzzily (plan 03 §5.12)."""

    def __init__(self, sources: Sequence[BuyerQuestionSource]) -> None:
        self._sources = sorted(sources, key=lambda source: -source.precedence)

    async def collect(
        self,
        client_id: UUID,
        profile: BusinessProfile,
        claims: ClaimFactory,
        existing: Sequence[BuyerQuestion] = (),
    ) -> list[BuyerQuestion]:
        cities = profile.locations.cities
        primary_offerings = {offering.id for offering in profile.offerings if offering.is_primary}
        primary_cta = next((action.id for action in profile.conversion_actions if action.is_primary), None)
        kept: list[BuyerQuestion] = list(existing)
        by_norm = {(question.normalized_text, question.language): question for question in existing}
        for source in self._sources:
            if not source.is_enabled(client_id):
                continue
            for candidate in await source.fetch(client_id, profile):
                normalized, geo = normalise_question(candidate.text, cities)
                if not normalized:
                    continue
                key = (normalized, candidate.language)
                if key in by_norm or any(
                    fuzz.token_set_ratio(normalized, other.normalized_text) >= DUPLICATE_MATCH
                    and other.language == candidate.language
                    for other in kept
                ):
                    continue
                question = self._question(
                    client_id, candidate, normalized, geo, claims, primary_offerings, primary_cta
                )
                kept.append(question)
                by_norm[key] = question
        return kept

    @staticmethod
    def _question(
        client_id: UUID,
        candidate: BuyerQuestionCandidate,
        normalized: str,
        geo: str | None,
        claims: ClaimFactory,
        primary_offerings: set[str],
        primary_cta: str | None,
    ) -> BuyerQuestion:
        intent, stage = classify_intent(candidate.text)
        source = candidate.source
        if source in {BuyerQuestionSourceKind.user, BuyerQuestionSourceKind.import_}:
            meta = ClaimMeta(
                origin=ClaimOrigin.user,
                status=ClaimStatus.confirmed,
                confidence=1.0,
                locked=True,
                first_seen_version=claims.version,
                updated_at=claims.now,
            )
            status = ReviewStatus.confirmed
        elif source is BuyerQuestionSourceKind.spotlit:
            meta = claims.based(candidate.basis_ids or ["spotlit"])
            status = ReviewStatus.confirmed
        elif candidate.evidence:
            meta = claims.evidenced(candidate.evidence)
            status = ReviewStatus.proposed
        else:
            meta = claims.based(candidate.basis_ids or candidate.offering_ids)
            status = ReviewStatus.proposed
        linked_primary = bool(set(candidate.offering_ids) & primary_offerings)
        priority = 1 if linked_primary else 2 if candidate.offering_ids else 3
        return BuyerQuestion(
            id=uuid.uuid5(client_id, f"{normalized}|{candidate.language}"),
            client_id=client_id,
            text=candidate.text,
            normalized_text=normalized,
            language=candidate.language,
            funnel_stage=candidate.funnel_stage or stage,
            intent=candidate.intent or intent,
            offering_ids=candidate.offering_ids,
            conversion_action_id=primary_cta if linked_primary else None,
            geo_modifier=geo,
            source=source,
            source_ref=candidate.source_ref,
            status=status,
            priority=priority,  # type: ignore[arg-type]
            ai_cited_video_ids=candidate.ai_cited_video_ids,
            meta=meta,
        )


def pretick(questions: Sequence[BuyerQuestion], count: int = PRETICK_COUNT) -> list[UUID]:
    """the ids the confirmation screen ticks by default: top questions by priority."""
    ranked = sorted(questions, key=lambda question: (question.priority, question.source.value, question.text))
    return [question.id for question in ranked if question.status is not ReviewStatus.rejected][:count]
