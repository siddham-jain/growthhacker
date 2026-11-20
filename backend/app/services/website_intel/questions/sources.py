from collections.abc import Sequence
from uuid import UUID

from app.domain.profile import BusinessProfile, BuyerQuestionSourceKind, FactKind
from app.ports.buyer_questions import BuyerQuestionCandidate
from app.services.website_intel.profile.evidence import FactStore


class WebsiteFaqSource:
    """faq questions stated on the site (json-ld FAQPage or faq-section facts); the answer quote stays as evidence."""

    source_id = BuyerQuestionSourceKind.website_faq
    precedence = 60

    def __init__(self, store: FactStore) -> None:
        self._store = store

    def is_enabled(self, client_id: UUID) -> bool:
        return True

    async def fetch(self, client_id: UUID, profile: BusinessProfile) -> list[BuyerQuestionCandidate]:
        candidates = []
        for fact in self._store.of_kind(FactKind.faq):
            question = fact.attrs.get("question") or fact.text
            candidates.append(
                BuyerQuestionCandidate(
                    text=question[:300],
                    language=fact.quote_lang or profile.identity.primary_language,
                    source=self.source_id,
                    source_ref=fact.source_url,
                    basis_ids=[fact.fact_id],
                    evidence=self._store.evidence(fact.fact_id),
                )
            )
        return candidates


class WebsiteDerivedSource:
    """questions generated in r2 from verified offerings; each carries basis ids and starts proposed."""

    source_id = BuyerQuestionSourceKind.website_derived
    precedence = 40

    def __init__(self, candidates: Sequence[BuyerQuestionCandidate]) -> None:
        self._candidates = list(candidates)

    def is_enabled(self, client_id: UUID) -> bool:
        return True

    async def fetch(self, client_id: UUID, profile: BusinessProfile) -> list[BuyerQuestionCandidate]:
        return list(self._candidates)


class UserQuestionSource:
    """questions the user typed, pasted (one per line) or imported."""

    source_id = BuyerQuestionSourceKind.user
    precedence = 100

    def __init__(self, texts: Sequence[str], *, language: str = "en") -> None:
        self._texts = [text.strip() for text in texts if text.strip()]
        self._language = language

    def is_enabled(self, client_id: UUID) -> bool:
        return bool(self._texts)

    async def fetch(self, client_id: UUID, profile: BusinessProfile) -> list[BuyerQuestionCandidate]:
        return [
            BuyerQuestionCandidate(text=text[:300], language=self._language, source=self.source_id)
            for text in self._texts
        ]


class SpotlitQuestionSource:
    """contract stub for spotlit's tracked questions (99 G12); disabled until the integration exists."""

    source_id = BuyerQuestionSourceKind.spotlit
    precedence = 80

    def __init__(self, *, enabled: bool = False) -> None:
        self._enabled = enabled

    def is_enabled(self, client_id: UUID) -> bool:
        return self._enabled

    async def fetch(self, client_id: UUID, profile: BusinessProfile) -> list[BuyerQuestionCandidate]:
        if not self._enabled:
            return []
        raise NotImplementedError("spotlit has no published api yet; keep the source disabled")
