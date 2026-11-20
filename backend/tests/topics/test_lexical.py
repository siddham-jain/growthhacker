import pytest

from app.core.config import TopicEngineSettings
from app.services.topics.universe.expansion.base import CorpusFrequencies
from tests.topics.fixtures import APOLLO_CLIENT, keyword, offering, prepare, profile, question, video_text
from tests.topics.golden import apollo
from tests.topics.helpers import lexical, universe_for

HEALTH = profile(
    APOLLO_CLIENT,
    name="Apollo Hospitals",
    offerings=[
        offering("o_knee", "Knee Replacement", aliases=["total knee replacement"]),
        offering("o_screen", "Breast Cancer Screening"),
        offering("o_ivf", "IVF"),
        offering("o_art", "Art Therapy"),
    ],
    keywords=[keyword("k_cancer", "cancer"), keyword("k_bypass", "bypass surgery"), keyword("k_tkr", "TKR")],
    brand_terms=["Apollo"],
)


@pytest.fixture
async def built():
    return await universe_for(HEALTH)


async def score(built, title: str, description: str = "", tags=(), settings=None, corpus=None):
    video = prepare(video_text(title, description, tags), settings)
    return (await lexical(built, [video], settings, corpus)).get(video.video_id, {})


async def test_exact_phrase_in_title_scores_full_weight(built) -> None:
    result = (await score(built, "Knee Replacement explained"))["Knee Replacement"]
    assert result.score == pytest.approx(1.0)
    evidence = result.evidence[0]
    assert (evidence.field, evidence.span_text, evidence.detail["hit_type"]) == (
        "title",
        "Knee Replacement",
        "exact",
    )


async def test_whole_tokens_only(built) -> None:
    results = await score(built, "Heart health and IVFX lab results")
    assert "Art Therapy" not in results and "IVF" not in results


async def test_inflections_meet_on_stem_keys(built) -> None:
    assert "Knee Replacement" in await score(built, "Knee replacements in older adults")


async def test_slop_phrase_within_one_sentence(built) -> None:
    result = (await score(built, "Knee joint replacement: what to expect"))["Knee Replacement"]
    assert result.evidence[0].detail["hit_type"] == "slop"
    assert result.score == pytest.approx(0.85)
    across = (await score(built, "Knee pain | best hip replacement"))["Knee Replacement"]
    assert across.evidence[0].detail["hit_type"] == "bag"


async def test_fuzzy_misspelling_on_long_tokens(built) -> None:
    result = (await score(built, "Knee replacemnt recovery tips"))["Knee Replacement"]
    assert result.evidence[0].detail["hit_type"] == "fuzzy"
    assert 0.6 < result.score < 0.7


async def test_bag_of_words_anywhere_in_field(built) -> None:
    result = (await score(built, "Screening for cancer of the breast"))["Breast Cancer Screening"]
    assert result.evidence[0].detail["hit_type"] == "bag"
    assert result.score == pytest.approx(0.5)


async def test_abbreviation_needs_exact_case() -> None:
    built = await universe_for(apollo.PROFILE)
    result = (await score(built, "TKR recovery week by week"))["Knee Replacement"]
    assert result.evidence[0].detail["hit_type"] == "abbreviation"
    assert result.evidence[0].alias_kind == "abbreviation"
    assert "Knee Replacement" not in await score(built, "tkr recovery week by week")


async def test_longest_match_subsumes_the_shorter_ref(built) -> None:
    results = await score(built, "Breast cancer screening at 40")
    assert results["Breast Cancer Screening"].score == pytest.approx(1.0)
    cancer = results["cancer"]
    assert cancer.evidence[0].subsumed_by_ref is not None
    assert "subsumed_only" in cancer.flags
    assert cancer.score == pytest.approx(0.5)


async def test_parent_refs_are_exempt_from_subsumption() -> None:
    questions = [question(APOLLO_CLIENT, "How much does knee replacement cost?", offering_ids=["o_knee"])]
    built = await universe_for(HEALTH, questions)
    results = await score(built, "Knee replacement cost in India")
    assert results["Knee Replacement"].evidence[0].subsumed_by_ref is None
    bq = results["How much does knee replacement cost?"]
    assert bq.evidence[0].detail["hit_type"] == "entity_facet"
    assert bq.score == pytest.approx(0.85)


async def test_negated_hits_count_less_and_are_flagged(built) -> None:
    result = (await score(built, "Bypass surgery is not always needed: not bypass surgery but stents"))[
        "bypass surgery"
    ]
    assert any(item.negated for item in result.evidence)
    clean = (await score(built, "Bypass surgery explained"))["bypass surgery"]
    negated_only = (await score(built, "You do not need bypass surgery"))["bypass surgery"]
    assert "all_negated" in negated_only.flags
    assert negated_only.score == pytest.approx(clean.score * 0.3)


async def test_hits_per_field_saturate_at_two(built) -> None:
    result = (await score(built, "IVF IVF IVF IVF IVF"))["IVF"]
    assert len(result.evidence) == 2


async def test_field_weights_and_tag_stuffing(built) -> None:
    tag_only = (await score(built, "Morning routine", tags=["ivf"]))["IVF"]
    assert tag_only.score == pytest.approx(0.45)
    assert "tags_only" in tag_only.flags
    stuffed = (await score(built, "Morning routine", tags=["ivf", *[f"t{index}" for index in range(45)]]))[
        "IVF"
    ]
    assert stuffed.score == pytest.approx(0.225)


async def test_brand_terms_are_never_topical(built) -> None:
    keywords = profile(
        APOLLO_CLIENT, name="Apollo Hospitals", keywords=[keyword("k1", "Apollo")], brand_terms=["Apollo"]
    )
    branded = await universe_for(keywords)
    assert await score(branded, "Apollo Hospitals annual day") == {}


async def test_unprofiled_channel_lists_are_list_mentions() -> None:
    built = await universe_for(apollo.PROFILE)
    description = (
        "Centres of Excellence: Knee Replacement, Cardiac Bypass Surgery, IVF, Kidney Stone Treatment"
    )
    results = await score(built, "Doctors Day celebration", description)
    assert all("list_mention" in result.flags for result in results.values())
    assert max(result.score for result in results.values()) < 0.1


async def test_phonetic_skeleton_matches_hinglish_spellings() -> None:
    source = profile(
        APOLLO_CLIENT, name="Apollo", offerings=[offering("o", "Knee Pain Treatment", aliases=["घुटने का दर्द"])]
    )
    built = await universe_for(source)
    result = (await score(built, "Ghutney ka dard kaise theek kare"))["Knee Pain Treatment"]
    assert result.evidence[0].detail["hit_type"] == "phonetic"
    assert result.evidence[0].span_text == "Ghutney ka dard"
    assert "cross_lingual_only" in result.flags


async def test_negative_contexts_weaken_ambiguous_aliases() -> None:
    source = profile(APOLLO_CLIENT, name="Clinic", keywords=[keyword("k", "Max")])
    built = await universe_for(source)
    ref = built.universe.refs[0]
    built.universe = built.universe.model_copy(
        update={"refs": [ref.model_copy(update={"negative_contexts": ["movie"]})]}
    )
    plain = (await score(built, "Max explained"))["Max"]
    with_context = (await score(built, "Max movie trailer"))["Max"]
    assert "ambiguous_only" in plain.flags
    assert "negative_context" in with_context.flags
    assert with_context.score == pytest.approx(plain.score * 0.2)


async def test_corpus_specificity_marks_common_aliases_weak(built) -> None:
    corpus = CorpusFrequencies(documents=1000, df={"cancer": 600})
    result = (await score(built, "Cancer awareness walk", corpus=corpus))["cancer"]
    assert "weak_only" in result.flags
    assert result.score < 0.15


async def test_unconfirmed_aliases_can_be_switched_off() -> None:
    source = profile(APOLLO_CLIENT, name="Apollo", offerings=apollo.OFFERINGS)
    built = await universe_for(source)
    knee = next(ref for ref in built.universe.refs if ref.label == "Knee Replacement")
    proposal = knee.aliases[0].model_copy(
        update={
            "text": "ghutne ka operation",
            "norm_key": "ghutn ka oper",
            "phonetic_key": "ghutne ka operation",
            "kind": "transliteration",
            "origin": "llm_proposed",
            "status": "proposed",
            "weight": 0.7,
        }
    )
    refs = [
        ref.model_copy(update={"aliases": [*ref.aliases, proposal]}) if ref.id == knee.id else ref
        for ref in built.universe.refs
    ]
    built.universe = built.universe.model_copy(update={"refs": refs})
    assert (await score(built, "ghutne ka operation"))["Knee Replacement"].score == pytest.approx(0.7)
    off = TopicEngineSettings(use_unconfirmed_aliases=False)
    assert "Knee Replacement" not in await score(built, "ghutne ka operation", settings=off)
