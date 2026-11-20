import pytest

from app.core.config import TopicEngineSettings
from app.domain.profile import ClaimStatus, FunnelStage, ReviewStatus
from app.domain.topics import Facet, TopicKind
from app.ports.llm import LLMErrorKind
from app.services.topics.lexicon import load_lexicons
from app.services.topics.llm import UnlimitedSpend
from app.services.topics.universe.aliases import make_alias
from app.services.topics.universe.builder import TopicUniverseBuilder, UniverseInputs
from app.services.topics.universe.expansion.abbreviations import abbreviation_pairs, best_long_form
from app.services.topics.universe.expansion.base import CorpusFrequencies, ExpansionContext
from app.services.topics.universe.expansion.llm_proposer import LLMAliasProposer
from app.services.topics.universe.expansion.validator import AliasValidator
from app.services.topics.universe.facets import FacetExtractor
from app.services.topics.universe.fingerprint import ref_fingerprint
from tests.topics.fixtures import (
    APOLLO_CLIENT,
    NOW,
    FakeLLM,
    HashEmbedder,
    keyword,
    offering,
    profile,
    question,
)
from tests.topics.golden import apollo


def builder(
    llm: FakeLLM | None = None, encoder=None, settings: TopicEngineSettings | None = None
) -> TopicUniverseBuilder:
    settings = settings or TopicEngineSettings()
    proposer = LLMAliasProposer(llm, settings, UnlimitedSpend()) if llm else None
    return TopicUniverseBuilder(
        settings,
        load_lexicons(),
        engine_version="e-test",
        embedder_model_ids={},
        encoder=encoder,
        proposer=proposer,
    )


async def build(inputs: UniverseInputs, **kwargs):
    return await builder(**kwargs).build(inputs, built_at=NOW)


def by_label(universe):
    return {ref.label: ref for ref in universe.refs}


async def test_only_confirmed_items_become_refs_with_stable_ids() -> None:
    source = profile(
        APOLLO_CLIENT,
        name="Apollo",
        offerings=[
            offering("o1", "Knee Replacement"),
            offering("o2", "Hair Transplant", status=ClaimStatus.proposed),
        ],
        keywords=[keyword("k1", "heart attack"), keyword("k2", "rejected term", status=ClaimStatus.rejected)],
    )
    questions = [
        question(APOLLO_CLIENT, "How much does knee replacement cost?"),
        question(APOLLO_CLIENT, "Is IVF painful?", status=ReviewStatus.proposed),
    ]
    first = (await build(UniverseInputs(profile=source, buyer_questions=questions))).universe
    assert set(by_label(first)) == {
        "Knee Replacement",
        "heart attack",
        "How much does knee replacement cost?",
    }
    second = (
        await build(
            UniverseInputs(profile=source.model_copy(update={"version": 2}), buyer_questions=questions)
        )
    ).universe
    assert {ref.id for ref in first.refs} == {ref.id for ref in second.refs}
    assert first.universe_hash != second.universe_hash
    assert all(ref.id.startswith("tr_") and len(ref.id) == 15 for ref in first.refs)
    assert all(alias.anchor_text for ref in first.refs for alias in ref.aliases)


async def test_duplicates_merge_and_caps_warn() -> None:
    source = profile(
        APOLLO_CLIENT,
        name="Apollo",
        keywords=[
            keyword("k1", "knee replacement"),
            keyword("k2", "Knee Replacements"),
            keyword("k3", "ivf"),
        ],
    )
    universe = (
        await build(UniverseInputs(profile=source), settings=TopicEngineSettings(max_refs_keyword_offering=1))
    ).universe
    assert len(universe.refs) == 1
    assert {alias.kind for alias in universe.refs[0].aliases} == {"canonical"}
    assert "ref_cap_applied:1" in universe.warnings
    assert "thin_universe" in universe.warnings


async def test_question_facets_funnel_and_parent_links() -> None:
    built = await build(UniverseInputs(profile=apollo.PROFILE, buyer_questions=apollo.QUESTIONS))
    refs = by_label(built.universe)
    cost = refs["How much does knee replacement cost?"]
    assert cost.facets == [Facet.cost]
    assert cost.core_entity_terms == ["knee", "replacement"]
    assert cost.funnel_stage is FunnelStage.decision
    assert cost.parent_ids == [refs["Knee Replacement"].id]
    symptoms = refs["What are the symptoms of a heart attack?"]
    assert symptoms.parent_ids == [refs["heart attack"].id]
    assert refs["Knee Replacement"].facets == [] and refs["Knee Replacement"].funnel_stage is None


def test_funnel_stage_is_derived_from_facets_when_missing(lexicons) -> None:
    extractor = FacetExtractor(lexicons, ["Chennai"])
    assert extractor.funnel_stage(extractor.facets("best knee hospital near me")) is FunnelStage.decision
    assert extractor.funnel_stage(extractor.facets("heart attack ke lakshan")) is FunnelStage.awareness
    assert extractor.locality_terms("knee replacement cost in Chennai") == ["chennai"]


async def test_brand_terms_are_non_topical_and_brand_like_keywords_are_ambiguous() -> None:
    source = profile(APOLLO_CLIENT, name="Apollo", keywords=[keyword("k1", "Max")], brand_terms=["Apollo"])
    built = await build(UniverseInputs(profile=source, competitor_names=["Max", "Fortis Healthcare"]))
    assert {"Apollo", "Max", "Fortis Healthcare"} <= set(built.non_topical_terms)
    assert built.universe.refs[0].aliases[0].ambiguous


def test_schwartz_hearst_abbreviations() -> None:
    assert best_long_form("TKR", "patients choose Total Knee Replacement") == "Total Knee Replacement"
    pairs = abbreviation_pairs(
        "We offer Total Knee Replacement (TKR) and Coronary Artery Bypass Grafting (CABG)."
    )
    assert pairs == [("Total Knee Replacement", "TKR"), ("Coronary Artery Bypass Grafting", "CABG")]
    assert abbreviation_pairs("the price (USD) is high") == []


async def test_abbreviations_anchor_to_existing_aliases() -> None:
    refs = by_label((await build(UniverseInputs(profile=apollo.PROFILE))).universe)
    knee = {alias.text: alias for alias in refs["Knee Replacement"].aliases}
    assert knee["TKR"].kind == "abbreviation" and knee["TKR"].match_mode == "token_exact_case"
    assert knee["TKR"].anchor_text == "total knee replacement"
    assert "CABG" in {alias.text for alias in refs["Cardiac Bypass Surgery"].aliases}


async def test_llm_proposals_stay_proposed_and_transliterations_are_derived() -> None:
    llm = FakeLLM(proposals=apollo.PROPOSALS)
    built = await build(UniverseInputs(profile=apollo.PROFILE), llm=llm)
    knee = {alias.text: alias for alias in by_label(built.universe)["Knee Replacement"].aliases}
    hindi = knee["घुटने का प्रत्यारोपण"]
    assert (
        hindi.status == "proposed" and hindi.weight == pytest.approx(0.7) and hindi.origin == "llm_proposed"
    )
    assert (
        knee["ghutne ka pratyaropan"].kind == "transliteration"
        and knee["ghutne ka pratyaropan"].origin == "rule"
    )
    assert built.llm_calls == 1
    assert llm.calls[0]["prompt"] == "topic_alias_proposal"


async def test_proposer_failure_degrades_to_a_warning() -> None:
    built = await build(UniverseInputs(profile=apollo.PROFILE), llm=FakeLLM(error=LLMErrorKind.overloaded))
    assert "alias_proposals_unavailable:overloaded" in built.universe.warnings
    assert all(alias.origin != "llm_proposed" for ref in built.universe.refs for alias in ref.aliases)


async def test_previous_proposals_are_reused_and_decisions_applied() -> None:
    first = await build(UniverseInputs(profile=apollo.PROFILE), llm=FakeLLM(proposals=apollo.PROPOSALS))
    knee = by_label(first.universe)["Knee Replacement"]
    hinglish = next(alias for alias in knee.aliases if alias.text == "ghutne ka operation")
    decisions = {(knee.id, hinglish.norm_key): "confirmed"}
    llm = FakeLLM(proposals=apollo.PROPOSALS)
    second = await build(
        UniverseInputs(profile=apollo.PROFILE, previous=first.universe, alias_decisions=decisions), llm=llm
    )
    assert llm.calls == []
    confirmed = next(
        alias
        for alias in by_label(second.universe)["Knee Replacement"].aliases
        if alias.text == "ghutne ka operation"
    )
    assert confirmed.status == "confirmed" and confirmed.weight == 1.0
    assert by_label(second.universe)["Knee Replacement"].fingerprint != knee.fingerprint


async def test_question_near_duplicates_merge_with_same_facets() -> None:
    encoder = HashEmbedder()

    async def encode(texts):
        return encoder.embed(texts, "query")

    questions = [
        question(APOLLO_CLIENT, "How much does knee replacement cost?"),
        question(APOLLO_CLIENT, "how much does knee replacement cost"),
        question(APOLLO_CLIENT, "How much does a knee replacement cost?"),
    ]
    built = await build(UniverseInputs(profile=apollo.PROFILE, buyer_questions=questions), encoder=encode)
    asked = [ref for ref in built.universe.refs if ref.kind is TopicKind.buyer_question]
    assert len(asked) == 1
    assert "merged_questions:1" in built.universe.warnings


def make_ctx(**overrides) -> ExpansionContext:
    return ExpansionContext(settings=TopicEngineSettings(), lexicons=load_lexicons(), **overrides)


def proposed(text: str, kind: str, anchor: str = "Knee Replacement"):
    return make_alias(text, kind=kind, origin="llm_proposed", status="proposed", anchor=anchor, weight=0.7)


async def test_validator_rules() -> None:
    refs = (await build(UniverseInputs(profile=apollo.PROFILE))).universe.refs
    knee = next(ref for ref in refs if ref.label == "Knee Replacement")
    knee = knee.model_copy(
        update={
            "aliases": [
                *knee.aliases,
                proposed("knee replacement surgery for elderly patients in india", "synonym"),
                proposed("treatment", "synonym"),
                proposed("arthritis", "synonym"),
                proposed("ghutne ka operation", "transliteration"),
                proposed("xyzzy", "spelling_variant"),
            ]
        }
    )
    validator = AliasValidator(make_ctx(), {("arthritis", "Knee Replacement"): 0.48})
    reasons = {alias.text: validator.reason(alias, knee) for alias in knee.aliases}
    assert reasons["knee replacement surgery for elderly patients in india"] == "too_many_tokens"
    assert reasons["treatment"] == "generic"
    assert reasons["arthritis"] == "low_similarity"
    assert reasons["xyzzy"] == "skeleton_mismatch"
    assert reasons["Knee Replacement"] is None
    corpus = CorpusFrequencies(documents=100, df={"orthopaedic": 40})
    common = proposed("orthopaedics", "synonym")
    assert AliasValidator(make_ctx(corpus=corpus)).reason(common, knee) == "generic_corpus"


async def test_validator_collisions_drop_shared_aliases() -> None:
    source = profile(
        APOLLO_CLIENT,
        name="Apollo",
        offerings=[
            offering("o1", "Angioplasty", aliases=["stent"]),
            offering("o2", "Bypass", aliases=["stent"]),
        ],
    )
    built = await build(UniverseInputs(profile=source))
    refs = by_label(built.universe)
    statuses = {ref: {alias.text: alias.status for alias in refs[ref].aliases} for ref in refs}
    assert statuses["Angioplasty"]["stent"] == "rejected" and statuses["Bypass"]["stent"] == "rejected"
    assert "alias_collision:stent" in built.universe.warnings
    assert all(
        alias.status == "confirmed"
        for ref in refs.values()
        for alias in ref.aliases
        if alias.kind == "canonical"
    )


async def test_fingerprint_ignores_alias_order_but_tracks_status() -> None:
    ref = by_label((await build(UniverseInputs(profile=apollo.PROFILE))).universe)["Knee Replacement"]
    reordered = ref.model_copy(update={"aliases": list(reversed(ref.aliases))})
    assert ref_fingerprint(reordered) == ref.fingerprint
    demoted = [
        alias.model_copy(update={"status": "rejected"}) if alias.kind == "client_alias" else alias
        for alias in ref.aliases
    ]
    assert ref_fingerprint(ref.model_copy(update={"aliases": demoted})) != ref.fingerprint


def site_evidence(quote: str, extraction: str = "nav"):
    from app.domain.profile import Evidence

    return Evidence(
        source_kind="site_direct",
        extraction=extraction,
        source_url="https://example.com/",
        quote=quote,
        match_score=100.0,
        fetched_at=NOW,
    )


def evidenced(item, *quotes: tuple[str, str]):
    return item.model_copy(
        update={"meta": item.meta.model_copy(update={"evidence": [site_evidence(q, e) for q, e in quotes]})}
    )


async def test_parent_offerings_carry_child_terms_and_site_short_names() -> None:
    neuro = evidenced(offering("o1", "Neurosciences"), ("Neurosciences", "nav"))
    epilepsy = evidenced(
        offering("o2", "Epilepsy treatment"),
        ("Epilepsy", "nav"),
        ("Top Epilepsy Specialists in India", "page_text"),
    ).model_copy(update={"parent_id": "o1"})
    ambulance = evidenced(offering("o3", "Ambulance services"), ("Ambulance in Chennai", "nav"))
    source = profile(APOLLO_CLIENT, name="Apollo", offerings=[neuro, epilepsy, ambulance])
    source = source.model_copy(
        update={"locations": source.locations.model_copy(update={"cities": ["Chennai"]})}
    )
    refs = by_label((await build(UniverseInputs(profile=source))).universe)
    child = {
        alias.text: alias for alias in refs["Neurosciences"].aliases if alias.origin == "profile_extracted"
    }
    assert set(child) == {"Epilepsy treatment", "Epilepsy"}
    assert all(alias.status == "confirmed" and alias.weight == 0.85 for alias in child.values())
    own = {alias.text: alias.status for alias in refs["Epilepsy treatment"].aliases}
    # the child keeps its own short name although the parent carries it too
    assert own["Epilepsy"] == "confirmed"
    assert "Top Epilepsy Specialists in India" not in own
    assert {alias.text for alias in refs["Ambulance services"].aliases} == {"Ambulance services"}


async def test_brand_and_trust_keywords_are_not_topics() -> None:
    trust = keyword("k3", "NABH accredited").model_copy(update={"kind": "brand_adjacent"})
    source = profile(
        APOLLO_CLIENT,
        name="Apollo Hospitals",
        keywords=[keyword("k1", "Apollo Hospital India"), keyword("k2", "cancer care hospital"), trust],
    )
    built = await build(UniverseInputs(profile=source, competitor_names=["CARE Hospitals"]))
    assert set(by_label(built.universe)) == {"cancer care hospital"}


async def test_one_failed_proposal_batch_keeps_the_others() -> None:
    class FlakyLLM(FakeLLM):
        async def generate_structured(self, **kwargs):
            if not self.calls:
                self.calls.append({"prompt": "failed"})
                raise LLMError(LLMErrorKind.truncated, "cut off")
            return await super().generate_structured(**kwargs)

    from app.ports.llm import LLMError

    llm = FlakyLLM(proposals=apollo.PROPOSALS)
    built = await build(
        UniverseInputs(profile=apollo.PROFILE), llm=llm, settings=TopicEngineSettings(proposer_batch_refs=1)
    )
    assert "alias_proposals_unavailable:truncated" in built.universe.warnings
    assert any(alias.origin == "llm_proposed" for ref in built.universe.refs for alias in ref.aliases)
