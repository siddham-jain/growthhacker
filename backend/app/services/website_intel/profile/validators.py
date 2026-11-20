import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from app.core.registry import Registry
from app.domain.profile import BusinessProfile
from app.services.website_intel.extract.contact import contact_is_valid
from app.services.website_intel.extract.injection import contains_instruction
from app.services.website_intel.profile.canonical import brand_tokens
from app.services.website_intel.profile.claims import normalise_name
from app.services.website_intel.profile.non_offerings import business_words, non_offering_reason

DEMOGRAPHIC = re.compile(
    r"\b(?:aged?\s*\d|\d{2}\s*[-–]\s*\d{2}\s*(?:years|yrs)|years? old|women|men|male|female|gender|income|"
    r"affluent|wealthy|religio\w*|caste|ethnic\w*|married|divorced|millennials?|gen ?z|boomers?)\b",
    re.IGNORECASE,
)
COLLECTIONS = (
    "offerings",
    "value_propositions",
    "target_customers",
    "conversion_actions",
    "competitor_hints",
)


@dataclass(frozen=True)
class Issue:
    collection: str
    item_id: str
    code: str
    action: Literal["drop", "flag"]


class ProfileValidator(Protocol):
    def validate(self, profile: BusinessProfile) -> list[Issue]: ...


profile_validator_registry: Registry[ProfileValidator] = Registry("website_profile_validators")


def _items(profile: BusinessProfile) -> Iterator[tuple[str, str, Any, list[str]]]:
    for offering in profile.offerings:
        yield "offerings", offering.id, offering, [offering.name, offering.description or ""]
    for item in profile.value_propositions:
        yield "value_propositions", item.id, item, [item.statement]
    for item in profile.target_customers:
        yield "target_customers", item.id, item, [item.segment]
    for item in profile.conversion_actions:
        yield "conversion_actions", item.id, item, [item.label]
    for item in profile.competitor_hints:
        yield "competitor_hints", item.name, item, [item.name]


@profile_validator_registry.register("instruction_text")
class InstructionTextValidator:
    # schema confinement: free text that reads like an instruction never reaches downstream prompts
    def validate(self, profile: BusinessProfile) -> list[Issue]:
        return [
            Issue(collection, item_id, "instruction_like_text", "drop")
            for collection, item_id, _, texts in _items(profile)
            if any(contains_instruction(text) for text in texts if text)
        ]


@profile_validator_registry.register("demographics")
class DemographicValidator:
    # stated segments only: a demographic word must appear in the evidence quote (plan 03 §5.11.4)
    def validate(self, profile: BusinessProfile) -> list[Issue]:
        issues = []
        for collection, item_id, item, texts in _items(profile):
            if collection not in {"target_customers", "value_propositions"}:
                continue
            quotes = " ".join(evidence.quote for evidence in item.meta.evidence).casefold()
            for text in texts:
                if any(term.casefold() not in quotes for term in DEMOGRAPHIC.findall(text)):
                    issues.append(Issue(collection, item_id, "inferred_demographic", "drop"))
                    break
        return issues


@profile_validator_registry.register("competitor_is_own_brand")
class CompetitorSanityValidator:
    def validate(self, profile: BusinessProfile) -> list[Issue]:
        own = {normalise_name(term.term) for term in profile.brand_terms} | {
            normalise_name(profile.identity.name)
        }
        own |= {normalise_name(brand.name) for brand in profile.identity.related_brands}
        own_domains = set(profile.identity.website_domains) | {
            brand.domain for brand in profile.identity.related_brands if brand.domain
        }
        return [
            Issue("competitor_hints", hint.name, "own_brand_as_competitor", "drop")
            for hint in profile.competitor_hints
            if normalise_name(hint.name) in own or (hint.domain and hint.domain in own_domains)
        ]


@profile_validator_registry.register("contact_actions")
class ContactActionValidator:
    # pages extracted before the contact rules can still carry form buttons, bare dialling codes or short codes
    def validate(self, profile: BusinessProfile) -> list[Issue]:
        return [
            Issue("conversion_actions", action.id, "not_a_contact", "drop")
            for action in profile.conversion_actions
            if not contact_is_valid(action)
        ]


@profile_validator_registry.register("non_offerings")
class NonOfferingValidator:
    # calls to action, app-store links and bare navigation headings are not things the business sells;
    # the binder already moved real ctas into conversion actions
    def validate(self, profile: BusinessProfile) -> list[Issue]:
        brands = brand_tokens([profile.identity.name, *(term.term for term in profile.brand_terms)])
        niche = business_words(profile.identity.niche)
        return [
            Issue("offerings", offering.id, reason, "drop")
            for offering in profile.offerings
            if (reason := non_offering_reason(offering.name, offering.url, brands, niche))
        ]


@profile_validator_registry.register("duplicate_items")
class DuplicateValidator:
    def validate(self, profile: BusinessProfile) -> list[Issue]:
        issues = []
        seen: set[tuple[str, str]] = set()
        for collection, item_id, _, _ in _items(profile):
            key = (collection, item_id)
            if key in seen:
                issues.append(Issue(collection, item_id, "duplicate", "drop"))
            seen.add(key)
        return issues


class ValidatorChain:
    def __init__(self, registry: Registry[ProfileValidator] = profile_validator_registry) -> None:
        self._validators = [
            validator() if isinstance(validator, type) else validator for validator in registry.all().values()
        ]

    def apply(self, profile: BusinessProfile) -> tuple[BusinessProfile, list[Issue]]:
        issues = [issue for validator in self._validators for issue in validator.validate(profile)]
        update: dict[str, Any] = {}
        for collection in COLLECTIONS:
            flagged = [issue for issue in issues if issue.collection == collection and issue.action == "drop"]
            if not flagged:
                continue
            hard = {issue.item_id for issue in flagged if issue.code != "duplicate"}
            key = "name" if collection == "competitor_hints" else "id"
            kept, seen = [], set()
            for item in getattr(profile, collection):
                item_key = getattr(item, key)
                # duplicates keep their first occurrence; every other drop removes the item
                if item_key in hard or item_key in seen:
                    continue
                seen.add(item_key)
                kept.append(item)
            update[collection] = kept
        if "offerings" in update:
            self._unlink(profile, update)
        return (profile.model_copy(update=update) if update else profile), issues

    @staticmethod
    def _unlink(profile: BusinessProfile, update: dict[str, Any]) -> None:
        """children, value props and actions stop pointing at a dropped offering."""
        kept = {offering.id for offering in update["offerings"]}
        update["offerings"] = [
            item
            if item.parent_id in kept or item.parent_id is None
            else item.model_copy(update={"parent_id": None})
            for item in update["offerings"]
        ]
        for collection in ("value_propositions", "conversion_actions"):
            update[collection] = [
                item.model_copy(update={"offering_ids": [i for i in item.offering_ids if i in kept]})
                for item in update.get(collection, getattr(profile, collection))
            ]
