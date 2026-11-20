from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from app.domain.topics import TopicAlias, TopicRef
from app.services.topics.universe.expansion.base import ExpansionContext

CLIENT_KINDS = frozenset({"canonical", "client_alias"})
CLIENT_ORIGINS = frozenset({"client"})


def hierarchical(alias: TopicAlias) -> bool:
    # a narrower offering's name carried by its parent topic overlaps the child topic by design
    return alias.kind == "client_alias" and alias.origin == "profile_extracted"


@dataclass
class ValidationOutcome:
    refs: list[TopicRef]
    rejections: dict[tuple[str, str], str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


class AliasValidator:
    """deterministic anchoring checks; an alias that fails is kept as rejected so it is never re-proposed."""

    def __init__(self, ctx: ExpansionContext, cosines: Mapping[tuple[str, str], float] | None = None) -> None:
        self._ctx = ctx
        self._cosines = cosines or {}

    def reason(self, alias: TopicAlias, ref: TopicRef) -> str | None:
        # client terms and reviewer-confirmed proposals are never second-guessed
        if alias.kind in CLIENT_KINDS or alias.origin in CLIENT_ORIGINS:
            return None
        if alias.origin == "llm_proposed" and alias.status == "confirmed":
            return None
        settings, lexicons = self._ctx.settings, self._ctx.lexicons
        keys = alias.norm_key.split()
        if len(keys) > settings.alias_max_tokens:
            return "too_many_tokens"
        if all(key in lexicons.generic or key in lexicons.stopwords for key in keys):
            return "generic"
        if keys and min(self._ctx.corpus.share(key) for key in keys) > settings.generic_df:
            return "generic_corpus"
        translations = [
            item for item in ref.aliases if item.kind in {"translation", "canonical", "client_alias"}
        ]
        if alias.kind in {"synonym", "translation"}:
            cosine = self._cosines.get((alias.text, alias.anchor_text))
            if (
                cosine is not None
                and cosine < settings.alias_min_cos
                and not self._skeleton_match(alias, translations)
            ):
                return "low_similarity"
        if alias.kind in {"transliteration", "spelling_variant"} and not self._skeleton_match(
            alias, translations
        ):
            return "skeleton_mismatch"
        return None

    def _skeleton_match(self, alias: TopicAlias, anchors: Sequence[TopicAlias]) -> bool:
        if not alias.phonetic_key:
            return False
        threshold = self._ctx.settings.transliteration_min_ratio
        return any(
            anchor.phonetic_key and fuzz.ratio(alias.phonetic_key, anchor.phonetic_key) >= threshold
            for anchor in anchors
            if anchor is not alias
        )

    def validate(self, refs: Sequence[TopicRef]) -> ValidationOutcome:
        outcome = ValidationOutcome(refs=[])
        rejected: dict[tuple[str, str], str] = {}
        for ref in refs:
            for alias in ref.aliases:
                if alias.status == "rejected":
                    continue
                reason = self.reason(alias, ref)
                if reason:
                    rejected[(ref.id, alias.norm_key)] = reason
        self._collisions(refs, rejected, outcome.warnings)
        for ref in refs:
            aliases = []
            for alias in ref.aliases:
                reason = rejected.get((ref.id, alias.norm_key))
                if reason and alias.kind != "canonical":
                    alias = alias.model_copy(update={"status": "rejected"})
                    outcome.rejections[(ref.id, alias.norm_key)] = reason
                aliases.append(alias)
            outcome.refs.append(ref.model_copy(update={"aliases": aliases}))
        return outcome

    def _collisions(
        self, refs: Sequence[TopicRef], rejected: dict[tuple[str, str], str], warnings: list[str]
    ) -> None:
        # refs tied to the same offering describe one concept, so sharing a surface form is expected
        offerings = {ref.id: set(ref.offering_ids) for ref in refs}

        def linked(first: str, second: str) -> bool:
            return bool(offerings[first] & offerings[second])

        canonical_owner: dict[str, str] = {}
        for ref in refs:
            for alias in ref.aliases:
                if alias.kind == "canonical":
                    canonical_owner.setdefault(alias.norm_key, ref.id)
        owners: dict[str, list[tuple[str, TopicAlias]]] = {}
        for ref in refs:
            for alias in ref.aliases:
                if alias.kind != "canonical" and alias.status != "rejected" and not hierarchical(alias):
                    owners.setdefault(alias.norm_key, []).append((ref.id, alias))
        for key, entries in owners.items():
            owner = canonical_owner.get(key)
            if owner is not None:
                # an alias equal to another topic's own name belongs to that topic
                for ref_id, _ in entries:
                    if ref_id != owner and not linked(ref_id, owner):
                        rejected[(ref_id, key)] = "collision"
                continue
            ref_ids = sorted({ref_id for ref_id, _ in entries})
            clashing = [
                ref_id
                for ref_id in ref_ids
                if any(not linked(ref_id, other) for other in ref_ids if other != ref_id)
            ]
            if clashing:
                for ref_id in clashing:
                    rejected[(ref_id, key)] = "collision"
                warnings.append(f"alias_collision:{entries[0][1].text}")
