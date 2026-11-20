from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

import numpy as np
from numpy.typing import NDArray

from app.core.config import TopicEngineSettings
from app.domain.profile import (
    BusinessProfile,
    BuyerQuestion,
    ClaimStatus,
    FunnelStage,
    Offering,
    ReviewStatus,
)
from app.domain.topics import TopicAlias, TopicKind, TopicRef, TopicUniverse, TopicUniverseStats
from app.domain.web import ExtractionKind
from app.ports.buyer_questions import BuyerQuestionCandidate
from app.services.topics.lexicon import Lexicons
from app.services.topics.text.tokenize import stems
from app.services.topics.universe.aliases import AliasKind, AliasStatus, make_alias, norm_key
from app.services.topics.universe.expansion import CorpusFrequencies, ExpansionContext, expander_registry
from app.services.topics.universe.expansion.llm_proposer import LLMAliasProposer, Proposal
from app.services.topics.universe.expansion.validator import AliasValidator
from app.services.topics.universe.facets import FacetExtractor
from app.services.topics.universe.fingerprint import ref_fingerprint, ref_text, topic_ref_id, universe_hash

USABLE_CLAIMS = frozenset({ClaimStatus.confirmed, ClaimStatus.edited})
ALIAS_RANK = {"canonical": 0, "client_alias": 1}
SHORT_NAME_MAX_TOKENS = 6
# trust claims and brand slogans describe the business, not a subject a video can be about
NON_TOPICAL_KEYWORD_KINDS = frozenset({"brand_adjacent"})
STATUS_RANK = {"confirmed": 0, "proposed": 1, "rejected": 2}

TextEncoder = Callable[[Sequence[str]], Awaitable[NDArray[np.float32] | None]]


@dataclass
class _Seed:
    kind: TopicKind
    source_item_id: str
    label: str
    order: int
    definition: str | None = None
    client_aliases: list[str] = field(default_factory=list)
    # names of narrower offerings the client lists under this one (department -> treated condition)
    child_terms: list[str] = field(default_factory=list)
    lang: str | None = None
    funnel_stage: FunnelStage | None = None
    offering_ids: list[str] = field(default_factory=list)
    priority: int = 2


@dataclass(frozen=True)
class UniverseInputs:
    profile: BusinessProfile
    buyer_questions: Sequence[BuyerQuestion] = ()
    extra_questions: Sequence[BuyerQuestionCandidate] = ()
    competitor_names: Sequence[str] = ()
    alias_decisions: Mapping[tuple[str, str], AliasStatus] = field(default_factory=dict)
    previous: TopicUniverse | None = None
    corpus: CorpusFrequencies = field(default_factory=CorpusFrequencies)


@dataclass
class BuiltUniverse:
    universe: TopicUniverse
    non_topical_terms: list[str]
    rejections: dict[tuple[str, str], str]
    llm_calls: int = 0


def non_topical_terms(profile: BusinessProfile, competitor_names: Sequence[str]) -> list[str]:
    identity = profile.identity
    terms = [identity.name, identity.legal_name or "", *identity.brand_aliases]
    terms += [brand.name for brand in identity.related_brands]
    for brand in profile.brand_terms:
        if brand.kind != "slogan":
            terms += [brand.term, *brand.variants]
    terms += list(competitor_names)
    return sorted({term.strip() for term in terms if term and term.strip()})


class TopicUniverseBuilder:
    """turns the client's confirmed keywords, offerings and questions into anchored TopicRefs."""

    def __init__(
        self,
        settings: TopicEngineSettings,
        lexicons: Lexicons,
        *,
        engine_version: str,
        embedder_model_ids: Mapping[str, str],
        encoder: TextEncoder | None = None,
        proposer: LLMAliasProposer | None = None,
    ) -> None:
        self._settings = settings
        self._lexicons = lexicons
        self._engine_version = engine_version
        self._embedder_model_ids = dict(embedder_model_ids)
        self._encoder = encoder
        self._proposer = proposer

    async def build(
        self, inputs: UniverseInputs, *, built_at: datetime, run_id: UUID | None = None
    ) -> BuiltUniverse:
        profile = inputs.profile
        warnings: list[str] = []
        brands = non_topical_terms(profile, inputs.competitor_names)
        extractor = FacetExtractor(self._lexicons, profile.locations.cities)
        entity_seeds = self._cap(
            self._dedupe(self._entity_seeds(profile, extractor, non_topical_terms(profile, ()))),
            self._settings.max_refs_keyword_offering,
            "ref_cap_applied",
            warnings,
        )
        question_seeds = self._cap(
            self._dedupe(self._question_seeds(inputs)),
            self._settings.max_buyer_questions,
            "question_cap_applied",
            warnings,
        )
        refs = [self._ref(profile, seed, extractor, brands) for seed in [*entity_seeds, *question_seeds]]
        refs = await self._merge_questions(refs, warnings)
        refs = await self._propose(refs, profile, inputs.previous, run_id)
        ctx = ExpansionContext(
            settings=self._settings,
            lexicons=self._lexicons,
            profile_texts=_profile_texts(profile, inputs.buyer_questions),
            domain_kind=profile.freshness.domain_kind,
            corpus=inputs.corpus,
        )
        # proposals are validated before rule expansion so a rejected translation spawns no transliteration
        refs = [self._apply_decisions(ref, inputs.alias_decisions) for ref in refs]
        validator = AliasValidator(ctx, await self._alias_cosines(refs))
        proposals = validator.validate(refs)
        refs = [self._expand(ref, ctx) for ref in proposals.refs]
        refs = [self._apply_decisions(ref, inputs.alias_decisions) for ref in refs]
        outcome = validator.validate(refs)
        warnings += list(dict.fromkeys([*proposals.warnings, *outcome.warnings]))
        rejections = {**proposals.rejections, **outcome.rejections}
        refs = await self._link_parents(outcome.refs, warnings)
        refs = [ref.model_copy(update={"fingerprint": ref_fingerprint(ref)}) for ref in refs]
        warnings += self._thin_warnings(refs)
        universe = TopicUniverse(
            client_id=profile.client_id,
            profile_version=profile.version,
            universe_hash=universe_hash(
                refs, profile.version, self._engine_version, self._embedder_model_ids
            ),
            engine_version=self._engine_version,
            embedder_model_ids=self._embedder_model_ids,
            refs=refs,
            stats=universe_stats(refs),
            warnings=[*warnings, *(self._proposer.warnings if self._proposer else [])],
            built_at=built_at,
        )
        return BuiltUniverse(
            universe=universe,
            non_topical_terms=brands,
            rejections=rejections,
            llm_calls=self._proposer.calls if self._proposer else 0,
        )

    def _entity_seeds(
        self, profile: BusinessProfile, extractor: FacetExtractor, own_brands: Sequence[str]
    ) -> list[_Seed]:
        seeds: list[_Seed] = []
        offerings = sorted(
            (offering for offering in profile.offerings if offering.meta.status in USABLE_CLAIMS),
            key=lambda offering: offering.priority,
        )
        short_names = {offering.id: short_names_of(offering, extractor) for offering in offerings}
        children: dict[str, list[str]] = {}
        for offering in offerings:
            if offering.parent_id and offering.parent_id in short_names:
                children.setdefault(offering.parent_id, []).extend([offering.name, *short_names[offering.id]])
        for offering in offerings:
            aliases = [*offering.aliases, *([offering.name_original] if offering.name_original else [])]
            seeds.append(
                _Seed(
                    kind=TopicKind.offering,
                    source_item_id=offering.id,
                    label=offering.name,
                    order=len(seeds),
                    definition=offering.definition or offering.description,
                    client_aliases=[*aliases, *short_names[offering.id]],
                    child_terms=children.get(offering.id, []),
                    offering_ids=[offering.id],
                    priority=offering.priority,
                    lang=profile.identity.primary_language,
                )
            )
        brand_phrases = [stems(term) for term in own_brands]
        keywords = sorted(
            (
                keyword
                for keyword in profile.niche_keywords
                if keyword.meta.status in USABLE_CLAIMS
                and keyword.kind not in NON_TOPICAL_KEYWORD_KINDS
                and not mentions_any(keyword.term, brand_phrases)
            ),
            key=lambda keyword: -keyword.weight,
        )
        for keyword in keywords:
            seeds.append(
                _Seed(
                    kind=TopicKind.keyword,
                    source_item_id=keyword.id,
                    label=keyword.term,
                    order=len(seeds),
                    lang=keyword.language,
                    offering_ids=list(keyword.offering_ids),
                )
            )
        return seeds

    def _question_seeds(self, inputs: UniverseInputs) -> list[_Seed]:
        seeds = [
            _Seed(
                kind=TopicKind.buyer_question,
                source_item_id=str(question.id),
                label=question.text,
                order=index,
                lang=question.language,
                funnel_stage=question.funnel_stage,
                offering_ids=list(question.offering_ids),
                priority=question.priority,
            )
            for index, question in enumerate(inputs.buyer_questions)
            if question.status == ReviewStatus.confirmed
        ]
        for candidate in inputs.extra_questions:
            seeds.append(
                _Seed(
                    kind=TopicKind.buyer_question,
                    source_item_id=candidate.source_ref
                    or f"{candidate.source.value}:{norm_key(candidate.text)}",
                    label=candidate.text,
                    order=len(seeds),
                    lang=candidate.language,
                    funnel_stage=candidate.funnel_stage,
                    offering_ids=list(candidate.offering_ids),
                )
            )
        return sorted(seeds, key=lambda seed: (seed.priority, seed.order))

    def _dedupe(self, seeds: list[_Seed]) -> list[_Seed]:
        kept: dict[tuple[TopicKind, str], _Seed] = {}
        for seed in seeds:
            key = (seed.kind, norm_key(seed.label))
            if not key[1]:
                continue
            existing = kept.get(key)
            if existing is None:
                kept[key] = seed
                continue
            existing.client_aliases += [seed.label, *seed.client_aliases]
            existing.child_terms += seed.child_terms
            existing.offering_ids = list(dict.fromkeys([*existing.offering_ids, *seed.offering_ids]))
        return list(kept.values())

    def _cap(self, seeds: list[_Seed], limit: int, warning: str, warnings: list[str]) -> list[_Seed]:
        if len(seeds) <= limit:
            return seeds
        warnings.append(f"{warning}:{len(seeds) - limit}")
        return seeds[:limit]

    def _ref(
        self, profile: BusinessProfile, seed: _Seed, extractor: FacetExtractor, brands: Sequence[str]
    ) -> TopicRef:
        brand_keys = {norm_key(term) for term in brands}
        forms: list[tuple[str, AliasKind]] = [(seed.label, "canonical")]
        forms += [(text, "client_alias") for text in seed.client_aliases]
        aliases = [
            alias
            for text, kind in forms
            if (alias := self._client_alias(text, kind, seed, brand_keys)) is not None
        ]
        aliases += [
            alias
            for text in seed.child_terms
            if (alias := self._child_alias(text, seed, brand_keys)) is not None
        ]
        facets, core, locality, stage = [], [], [], None
        if seed.kind is TopicKind.buyer_question:
            facets = extractor.facets(seed.label)
            core = extractor.core_entity_terms(seed.label)
            locality = extractor.locality_terms(seed.label)
            stage = seed.funnel_stage or extractor.funnel_stage(facets)
        return TopicRef(
            id=topic_ref_id(profile.client_id, seed.kind, norm_key(seed.label)),
            client_id=profile.client_id,
            profile_version=profile.version,
            kind=seed.kind,
            source_item_id=seed.source_item_id,
            label=seed.label,
            definition=seed.definition,
            aliases=_dedupe_aliases(aliases),
            offering_ids=seed.offering_ids,
            funnel_stage=stage,
            facets=facets,
            core_entity_terms=core,
            locality_terms=locality,
            fingerprint="pending",
        )

    def _client_alias(
        self, text: str, kind: AliasKind, seed: _Seed, brand_keys: set[str]
    ) -> TopicAlias | None:
        key = norm_key(text)
        ambiguous = len(key.split()) == 1 and (key in self._lexicons.common_words or key in brand_keys)
        return make_alias(
            text,
            kind=kind,
            origin="client",
            status="confirmed",
            anchor=seed.label,
            weight=1.0,
            lang=seed.lang,
            ambiguous=ambiguous,
        )

    def _child_alias(self, text: str, seed: _Seed, brand_keys: set[str]) -> TopicAlias | None:
        alias = self._client_alias(text, "client_alias", seed, brand_keys)
        if alias is None:
            return None
        return alias.model_copy(
            update={"origin": "profile_extracted", "weight": self._settings.child_term_weight}
        )

    async def _merge_questions(self, refs: list[TopicRef], warnings: list[str]) -> list[TopicRef]:
        questions = [ref for ref in refs if ref.kind is TopicKind.buyer_question]
        if len(questions) < 2 or self._encoder is None:
            return refs
        vectors = await self._encoder([ref.label for ref in questions])
        if vectors is None:
            return refs
        merged: set[str] = set()
        kept: list[tuple[TopicRef, NDArray[np.float32]]] = []
        for ref, vector in zip(questions, vectors, strict=True):
            target = next(
                (
                    index
                    for index, (other, other_vector) in enumerate(kept)
                    if set(other.facets) == set(ref.facets)
                    and float(other_vector @ vector) >= self._settings.bq_merge_cos
                ),
                None,
            )
            if target is None:
                kept.append((ref, vector))
                continue
            other, other_vector = kept[target]
            extra = [alias.model_copy(update={"kind": "client_alias"}) for alias in ref.aliases]
            updated = other.model_copy(
                update={
                    "aliases": _dedupe_aliases([*other.aliases, *extra]),
                    "offering_ids": list(dict.fromkeys([*other.offering_ids, *ref.offering_ids])),
                }
            )
            kept[target] = (updated, other_vector)
            merged.add(ref.id)
        if merged:
            warnings.append(f"merged_questions:{len(merged)}")
        kept_by_id = {ref.id: ref for ref, _ in kept}
        return [kept_by_id.get(ref.id, ref) for ref in refs if ref.id not in merged]

    async def _propose(
        self,
        refs: list[TopicRef],
        profile: BusinessProfile,
        previous: TopicUniverse | None,
        run_id: UUID | None,
    ) -> list[TopicRef]:
        reused: dict[str, Proposal] = {}
        previous_refs = {ref.id: ref for ref in previous.refs} if previous else {}
        # empty proposals are only final when the previous build actually reached the proposer
        proposer_ran = (
            previous is not None
            and not any(warning.startswith("alias_proposals_unavailable") for warning in previous.warnings)
            and any(alias.origin == "llm_proposed" for ref in previous.refs for alias in ref.aliases)
        )
        to_propose = []
        for ref in refs:
            old = previous_refs.get(ref.id)
            old_proposals = [alias for alias in old.aliases if alias.origin == "llm_proposed"] if old else []
            if old is not None and old.definition == ref.definition and (old_proposals or proposer_ran):
                reused[ref.id] = Proposal(
                    aliases=old_proposals, negative_contexts=list(old.negative_contexts)
                )
            elif ref.kind is not TopicKind.buyer_question:
                to_propose.append(ref)
        proposals = dict(reused)
        if self._proposer is not None and to_propose:
            languages = list(dict.fromkeys(profile.identity.languages or self._settings.default_languages))
            if "hi" in languages and "hi-Latn" not in languages:
                languages.append("hi-Latn")
            proposals.update(
                await self._proposer.propose(
                    to_propose, languages, client_id=profile.client_id, run_id=run_id
                )
            )
        updated = []
        for ref in refs:
            proposal = proposals.get(ref.id)
            if proposal is None:
                updated.append(ref)
                continue
            updated.append(
                ref.model_copy(
                    update={
                        "aliases": _dedupe_aliases([*ref.aliases, *proposal.aliases]),
                        "negative_contexts": list(
                            dict.fromkeys([*ref.negative_contexts, *proposal.negative_contexts])
                        ),
                    }
                )
            )
        return updated

    def _expand(self, ref: TopicRef, ctx: ExpansionContext) -> TopicRef:
        aliases = list(ref.aliases)
        for name in sorted(expander_registry):
            expander = expander_registry.get(name)()
            aliases += expander.expand(ref.model_copy(update={"aliases": aliases}), ctx)
        return ref.model_copy(update={"aliases": _dedupe_aliases(aliases)})

    def _apply_decisions(self, ref: TopicRef, decisions: Mapping[tuple[str, str], AliasStatus]) -> TopicRef:
        aliases = []
        for alias in ref.aliases:
            decision = decisions.get((ref.id, alias.norm_key))
            if decision == "confirmed" and alias.status != "confirmed":
                alias = alias.model_copy(update={"status": "confirmed", "weight": 1.0})
            elif decision == "rejected" and alias.kind != "canonical":
                alias = alias.model_copy(update={"status": "rejected"})
            aliases.append(alias)
        return ref.model_copy(update={"aliases": aliases})

    async def _alias_cosines(self, refs: Sequence[TopicRef]) -> dict[tuple[str, str], float]:
        pairs = sorted(
            {
                (alias.text, alias.anchor_text)
                for ref in refs
                for alias in ref.aliases
                if alias.kind in {"synonym", "translation"} and alias.status == "proposed"
            }
        )
        if not pairs or self._encoder is None:
            return {}
        texts = list(dict.fromkeys(text for pair in pairs for text in pair))
        vectors = await self._encoder(texts)
        if vectors is None:
            return {}
        index = {text: vectors[position] for position, text in enumerate(texts)}
        return {(alias, anchor): float(index[alias] @ index[anchor]) for alias, anchor in pairs}

    async def _link_parents(self, refs: list[TopicRef], warnings: list[str]) -> list[TopicRef]:
        parents = [ref for ref in refs if ref.kind in {TopicKind.keyword, TopicKind.offering}]
        by_offering: dict[str, list[str]] = {}
        for parent in parents:
            if parent.kind is TopicKind.offering:
                for offering_id in parent.offering_ids:
                    by_offering.setdefault(offering_id, []).append(parent.id)
        unlinked: list[int] = []
        linked = list(refs)
        for position, ref in enumerate(refs):
            if ref.kind is not TopicKind.buyer_question:
                continue
            parent_ids = [
                ref_id for offering_id in ref.offering_ids for ref_id in by_offering.get(offering_id, [])
            ]
            parent_ids += _lexical_parents(ref, parents)
            if parent_ids:
                linked[position] = ref.model_copy(update={"parent_ids": list(dict.fromkeys(parent_ids))})
            else:
                unlinked.append(position)
        if unlinked and parents and self._encoder is not None:
            vectors = await self._encoder(
                [refs[i].label for i in unlinked] + [ref_text(parent) for parent in parents]
            )
            if vectors is not None:
                questions, candidates = vectors[: len(unlinked)], vectors[len(unlinked) :]
                for position, vector in zip(unlinked, questions, strict=True):
                    scores = candidates @ vector
                    best = int(np.argmax(scores))
                    if float(scores[best]) >= self._settings.parent_link_cos:
                        linked[position] = refs[position].model_copy(
                            update={"parent_ids": [parents[best].id]}
                        )
                        warnings.append(f"proposed_parent_link:{refs[position].id}:{parents[best].id}")
        return linked

    def _thin_warnings(self, refs: Sequence[TopicRef]) -> list[str]:
        entities = sum(1 for ref in refs if ref.kind in {TopicKind.keyword, TopicKind.offering})
        questions = sum(1 for ref in refs if ref.kind is TopicKind.buyer_question)
        if entities < self._settings.min_refs or questions < self._settings.min_buyer_questions:
            return ["thin_universe"]
        return []


def mentions_any(text: str, phrases: Sequence[Sequence[str]]) -> bool:
    """true when the text contains one of the stemmed phrases, e.g. a keyword built on the brand name."""
    keys = list(stems(text))
    return any(
        phrase and any(keys[index : index + len(phrase)] == list(phrase) for index in range(len(keys)))
        for phrase in phrases
    )


def short_names_of(offering: Offering, extractor: FacetExtractor) -> list[str]:
    """the site's own short names for an offering: sub-phrases of its name and its navigation labels."""
    label_stems = set(stems(offering.name))
    names = []
    for evidence in offering.meta.evidence:
        quote = " ".join(evidence.quote.split())
        quote_stems = list(stems(quote))
        if (
            not quote_stems
            or len(quote_stems) > SHORT_NAME_MAX_TOKENS
            or norm_key(quote) == norm_key(offering.name)
        ):
            continue
        sub_phrase = set(quote_stems) < label_stems
        nav_label = evidence.extraction is ExtractionKind.nav and not extractor.locality_terms(quote)
        if sub_phrase or nav_label:
            names.append(quote)
    return list(dict.fromkeys(names))


def _lexical_parents(question: TopicRef, parents: Sequence[TopicRef]) -> list[str]:
    keys = list(stems(question.label))
    best_length, best = 0, []
    for parent in parents:
        for alias in parent.aliases:
            if alias.status == "rejected":
                continue
            pin = alias.norm_key.split()
            if len(pin) < best_length or not any(
                keys[index : index + len(pin)] == pin for index in range(len(keys) - len(pin) + 1)
            ):
                continue
            if len(pin) > best_length:
                best_length, best = len(pin), []
            best.append(parent.id)
    return list(dict.fromkeys(best))


def _dedupe_aliases(aliases: Sequence[TopicAlias]) -> list[TopicAlias]:
    best: dict[str, TopicAlias] = {}
    for alias in aliases:
        current = best.get(alias.norm_key)
        if current is None or _alias_rank(alias) < _alias_rank(current):
            best[alias.norm_key] = alias
    return list(best.values())


def _alias_rank(alias: TopicAlias) -> tuple[int, int]:
    return ALIAS_RANK.get(alias.kind, 2), STATUS_RANK[alias.status]


def _profile_texts(profile: BusinessProfile, questions: Sequence[BuyerQuestion]) -> list[str]:
    texts = [profile.identity.one_liner, profile.identity.description or ""]
    for offering in profile.offerings:
        texts += [offering.name, offering.description or "", offering.definition or ""]
        texts += [evidence.quote for evidence in offering.meta.evidence]
    for keyword in profile.niche_keywords:
        texts += [keyword.term, *(evidence.quote for evidence in keyword.meta.evidence)]
    texts += [proposition.statement for proposition in profile.value_propositions]
    texts += [question.text for question in questions]
    return [text for text in texts if text]


def universe_stats(refs: Sequence[TopicRef]) -> TopicUniverseStats:
    aliases = [alias for ref in refs for alias in ref.aliases if alias.kind != "canonical"]
    return TopicUniverseStats(
        keywords=sum(1 for ref in refs if ref.kind is TopicKind.keyword),
        offerings=sum(1 for ref in refs if ref.kind is TopicKind.offering),
        buyer_questions=sum(1 for ref in refs if ref.kind is TopicKind.buyer_question),
        aliases_confirmed=sum(1 for alias in aliases if alias.status == "confirmed"),
        aliases_proposed=sum(1 for alias in aliases if alias.status == "proposed"),
    )
