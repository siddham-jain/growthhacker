from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field

from app.domain.news import GroundedEntity, NewsItem, NewsJudgement, OfferingLink, RelevanceBand
from app.domain.profile import WatchEntity
from app.services.news.judge import AliasMap, EntityLLM, NewsJudgementLLM
from app.services.news.text import contains_phrase, normalize
from app.services.news.tiers import TIER1
from app.services.news.versions import version_tokens

REFRESH_KINDS = frozenset({"model", "product", "drug", "policy"})
BAND_ORDER = list(RelevanceBand)


@dataclass(frozen=True)
class GroundingResult:
    judgement: NewsJudgement | None
    aliases: list[str] = field(default_factory=list)
    refresh_keywords: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    reason: str | None = None


def _surface_text(item: NewsItem) -> str:
    return f"{item.title} {item.snippet}"


def entity_is_grounded(entity: EntityLLM, item: NewsItem | None) -> bool:
    if item is None or not entity.text.strip():
        return False
    text, quote = normalize(entity.text), normalize(entity.quote)
    return text in quote and quote in normalize(_surface_text(item))


class GroundingValidator:
    """deterministic hallucination firewall between the judgement call and every downstream number."""

    def __init__(self, alias_pairs: Sequence[tuple[str, str]] = ()) -> None:
        self._alias_pairs = list(alias_pairs)

    def _equivalents(self, text: str) -> list[str]:
        norm = normalize(text)
        found = []
        for left, right in self._alias_pairs:
            if norm == normalize(left):
                found.append(right)
            elif norm == normalize(right):
                found.append(left)
        return found

    def _canonical(self, text: str, members: Sequence[NewsItem], watch: Sequence[WatchEntity]) -> str:
        versions = version_tokens(text)
        for entity in watch:
            for name in (entity.name, *entity.aliases):
                if normalize(name) == normalize(text) and version_tokens(name) == versions:
                    return entity.name
        forms = Counter()
        tier1_forms = Counter()
        for item in members:
            surface = _surface_text(item)
            for candidate in (text, *self._equivalents(text)):
                if contains_phrase(surface, candidate):
                    start = normalize(surface).find(normalize(candidate))
                    original = _original_slice(surface, start, len(normalize(candidate))) or text
                    forms[original] += 1
                    if item.source_tier in TIER1:
                        tier1_forms[original] += 1
        pool = tier1_forms or forms
        canonical = max(pool, key=lambda form: (pool[form], form)) if pool else text
        return canonical if version_tokens(canonical) == versions else text

    def _entity(
        self, raw: EntityLLM, aliases: AliasMap, members: Sequence[NewsItem], watch: Sequence[WatchEntity]
    ) -> GroundedEntity | None:
        item = aliases.items.get(raw.item_ref)
        if not entity_is_grounded(raw, item):
            return None
        return GroundedEntity(
            text=raw.text,
            canonical=self._canonical(raw.text, members, watch),
            kind=raw.kind,
            evidence_item_id=item.id,
            evidence_quote=raw.quote,
        )

    def validate(
        self, output: NewsJudgementLLM, aliases: AliasMap, watch: Sequence[WatchEntity]
    ) -> GroundingResult:
        members = list(aliases.items.values())
        dropped: list[str] = []
        key = self._entity(output.key_entity, aliases, members, watch)
        if key is None:
            return GroundingResult(
                None, dropped=[f"entity:{output.key_entity.text}"], reason="ungrounded_entity"
            )
        others = []
        for raw in output.other_entities:
            entity = self._entity(raw, aliases, members, watch)
            if entity is None:
                dropped.append(f"entity:{raw.text}")
            else:
                others.append(entity)
        links = []
        for link in output.offering_links:
            offering_id = aliases.offerings.get(link.offering_ref)
            if offering_id is None:
                dropped.append(f"offering:{link.offering_ref}")
                continue
            value_props = [
                aliases.value_props[ref] for ref in link.value_prop_refs if ref in aliases.value_props
            ]
            dropped += [f"value_prop:{ref}" for ref in link.value_prop_refs if ref not in aliases.value_props]
            links.append(
                OfferingLink(offering_id=offering_id, value_prop_ids=value_props, rationale=link.rationale)
            )
        questions = [
            aliases.buyer_questions[ref]
            for ref in output.buyer_question_refs
            if ref in aliases.buyer_questions
        ]
        dropped += [
            f"question:{ref}" for ref in output.buyer_question_refs if ref not in aliases.buyer_questions
        ]
        competitors = [
            aliases.competitors[r] for r in output.about_competitor_refs if r in aliases.competitors
        ]
        dropped += [
            f"competitor:{ref}" for ref in output.about_competitor_refs if ref not in aliases.competitors
        ]
        action = aliases.conversion_actions.get(output.conversion_action_ref or "")
        if output.conversion_action_ref and action is None:
            dropped.append(f"action:{output.conversion_action_ref}")
        band = output.relevance_band
        if not links and BAND_ORDER.index(band) > BAND_ORDER.index(RelevanceBand.tangential):
            band = RelevanceBand.tangential
        judgement = NewsJudgement(
            relevance_band=band,
            key_entity=key,
            other_entities=others,
            event_type=output.event_type,
            event_date=output.event_date,
            confirmation=output.confirmation,
            offering_links=links,
            buyer_question_ids=questions,
            angle=output.angle,
            region_relevance=output.region_relevance,
            about_client=output.about_client,
            about_competitor_ids=competitors,
            safety_flags=list(dict.fromkeys(output.safety_flags)),
            conflicting_reports=output.conflicting_reports,
            conversion_action_id=action,
        )
        forms = [key.text, *self._equivalents(key.canonical), *self._equivalents(key.text)]
        seen = {normalize(key.canonical)}
        alias_forms = []
        for form in forms:
            if normalize(form) not in seen:
                seen.add(normalize(form))
                alias_forms.append(form)
        return GroundingResult(
            judgement,
            aliases=alias_forms,
            refresh_keywords=self._refresh_keywords(key, others, members),
            dropped=dropped,
        )

    def _refresh_keywords(
        self, key: GroundedEntity, others: Sequence[GroundedEntity], members: Sequence[NewsItem]
    ) -> list[str]:
        keywords = [key.canonical]
        for entity in others:
            if entity.kind not in REFRESH_KINDS:
                continue
            supporting = [item for item in members if contains_phrase(_surface_text(item), entity.text)]
            if len(supporting) >= 2 or any(item.source_tier in TIER1 for item in supporting):
                keywords.append(entity.canonical)
        return list(dict.fromkeys(keywords))


def _original_slice(surface: str, normalized_start: int, length: int) -> str | None:
    # maps a match in the normalised text back to the original casing when lengths line up
    if normalized_start < 0:
        return None
    collapsed = " ".join(surface.split())
    if len(normalize(collapsed)) != len(collapsed):
        return None
    return collapsed[normalized_start : normalized_start + length]
