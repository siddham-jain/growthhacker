import uuid

import pytest

from app.core.config import CompetitorSettings
from app.domain.common import SubscriberBand
from app.domain.competitors import (
    BusinessRelevance,
    CandidateRole,
    CandidateSource,
    ChannelEvidence,
    CompetitorRelation,
    GeoRef,
    OfferingMatch,
    OwnerKind,
    YouTubeRelevance,
)
from app.domain.youtube import OwnershipLabel, OwnershipVerdict
from app.services.competitors.model import CandidateEntity, ChannelLead
from app.services.competitors.score.business import BusinessRelevanceScorer
from app.services.competitors.score.confidence import candidate_confidence
from app.services.competitors.score.topics import PhraseTopicMatcher
from app.services.competitors.score.youtube import YouTubeAssessment, YouTubeRelevanceScorer, size_band
from app.services.competitors.select.assessment import AssessedCandidate
from app.services.competitors.select.auto_confirm import AutoConfirmPolicy
from app.services.competitors.select.proposer import SetProposer
from app.services.competitors.select.validation import (
    CompetitorSetValidator,
    SetSelection,
    SetValidationError,
)
from tests.competitors.apollo import apollo_profile
from tests.competitors.fakes import NOW, channel, video

# pinned so the cap assertions below do not move with the production default
SETTINGS = CompetitorSettings(proposal_size=5)


def entity(
    name,
    relation=CompetitorRelation.direct,
    kinds=("llm", "web_search"),
    geo=None,
    offerings=(),
    owner=OwnerKind.organisation,
):
    return CandidateEntity(
        key=name,
        brand_name=name,
        normalized_name=name.casefold(),
        relation=relation,
        owner_kind=owner,
        geography=geo or [],
        sources=[CandidateSource(kind=kind, ref="r", retrieved_at=NOW) for kind in kinds],
        offering_matches=[
            OfferingMatch(offering_id=oid, verdict="yes", quote="q", source_url="u") for oid in offerings
        ],
    )


@pytest.mark.parametrize(
    ("geo", "expected", "basis"),
    [
        ([GeoRef(city="Delhi", country_iso2="IN")], 1.0, "same city: Delhi"),
        ([GeoRef(region="Karnataka", country_iso2="IN")], 0.8, "same region: Karnataka"),
        ([GeoRef(country_iso2="IN")], 0.6, "same country: IN"),
        ([GeoRef(country_iso2="US")], 0.0, "no shared market"),
        ([], 0.3, "location unknown"),
    ],
)
def test_geo_overlap(geo, expected, basis):
    scorer = BusinessRelevanceScorer(apollo_profile(), SETTINGS)
    assert scorer.geo_overlap(entity("x", geo=geo)) == (expected, basis)


def test_business_tiers_and_profiles():
    scorer = BusinessRelevanceScorer(apollo_profile(), SETTINGS)
    strong = scorer.score(
        entity("Fortis", geo=[GeoRef(city="Delhi", country_iso2="IN")], offerings=["o1", "o2"])
    )
    assert strong.tier == "high" and strong.offering_overlap == pytest.approx(5 / 8)
    weak = scorer.score(entity("Unknown", kinds=("llm",), geo=[GeoRef(country_iso2="US")]))
    assert weak.tier == "low"
    benchmark = scorer.score(
        entity(
            "Mayo",
            CompetitorRelation.benchmark,
            geo=[GeoRef(country_iso2="US")],
            offerings=["o1", "o3", "o2"],
        )
    )
    assert benchmark.geo_overlap == 0.0 and benchmark.tier == "high"


def test_youtube_relevance_is_raw_signals_without_a_score():
    matcher = PhraseTopicMatcher.from_profile(apollo_profile())
    record = channel("UCrelevanceTestChannel01", "Test", subscribers=50_000)
    uploads = [
        video(f"vid{index:08d}", record.channel_id, title)
        for index, title in enumerate(
            ["Knee replacement tips", "Heart surgery", "Unrelated vlog", "Cardiology Q&A"]
        )
    ]
    item = ChannelLead(record.channel_id, "site_link", record=record, uploads=uploads)
    assessment = YouTubeRelevanceScorer(matcher, SETTINGS, client_subscribers=100_000).assess(item, NOW)
    assert assessment.matching_uploads == 3
    assert assessment.relevance.size_band == "peer"
    assert "score" not in YouTubeRelevance.model_fields


@pytest.mark.parametrize(
    ("subs", "client", "band"),
    [
        (1_000, 100_000, "much_smaller"),
        (10_000, 100_000, "smaller"),
        (100_000, 100_000, "peer"),
        (1_000_000, 100_000, "larger"),
        (10_000_000, 100_000, "much_larger"),
        (None, 100_000, "unknown"),
        (1_000, None, "unknown"),
    ],
)
def test_size_bands(subs, client, band):
    assert size_band(subs, client) == band


def assessed(
    name,
    *,
    tier="high",
    yt="high",
    matching=5,
    label=OwnershipLabel.verified,
    relation=CompetitorRelation.direct,
    uploads_90d=10,
    band="peer",
    owner=OwnerKind.organisation,
    flags=(),
    query_hits=0,
    score=0.9,
):
    target = entity(name, relation, owner=owner)
    target.flags.update(flags)
    target.query_hits = query_hits
    channel_id = f"UC{name.ljust(22, 'x')[:22]}"
    lead = ChannelLead(
        channel_id, "site_link", verdict=OwnershipVerdict(label=label, method="link", score=score, signals=[])
    )
    target.leads[channel_id] = lead
    evidence = ChannelEvidence(
        subscriber_count=1,
        video_count=10,
        country="IN",
        published_at=NOW,
        uploads_90d=uploads_90d,
        last_upload_at=NOW,
        fetched_at=NOW,
    )
    relevance = YouTubeRelevance(
        activity=0.5, content_overlap=0.3, size_band=band, subscriber_band=SubscriberBand.lt_10k
    )
    business = BusinessRelevance(
        offering_overlap=0.5, geo_overlap=1, geo_basis="b", source_agreement=1, tier=tier
    )
    item = AssessedCandidate(entity=target, business=business, primary=lead)
    item.assessments[channel_id] = YouTubeAssessment(
        evidence=evidence, relevance=relevance, matching_uploads=matching, tier=yt
    )
    return item


def test_ranking_keeps_web_tier_ahead_of_youtube_tier():
    web_strong = assessed("webstrong", tier="high", yt="low", matching=0)
    youtube_strong = assessed("ytstrong", tier="medium", yt="high", matching=50)
    assert sorted([youtube_strong, web_strong], key=lambda item: item.rank_key())[0] is web_strong


def test_proposal_caps_and_ordering():
    items = [assessed(f"direct{index}", matching=index) for index in range(7)]
    items.append(assessed("public", relation=CompetitorRelation.direct_public, flags=("public_institution",)))
    items.append(assessed("inactive", uploads_90d=0))
    items.append(assessed("uncertain", label=OwnershipLabel.uncertain))
    proposal = SetProposer(SETTINGS).propose(uuid.uuid4(), items)
    assert len(proposal.competitor_channel_ids) == 5 and proposal.ready
    chosen = {item.entity.brand_name for item in items if item.channel_id in proposal.competitor_channel_ids}
    assert chosen == {"direct6", "direct5", "direct4", "direct3", "direct2"}
    assert items[0].channel_id in proposal.niche_channel_ids
    excluded = {items[-1].channel_id, items[-2].channel_id}
    assert not excluded & {*proposal.competitor_channel_ids, *proposal.niche_channel_ids}


def test_few_directs_fill_with_content_competitors():
    items = [
        assessed("onlydirect"),
        assessed("practo", relation=CompetitorRelation.aggregator),
        assessed("explainer", relation=CompetitorRelation.content),
    ]
    proposal = SetProposer(SETTINGS).propose(uuid.uuid4(), items)
    assert len(proposal.competitor_channel_ids) == 3 and proposal.contains_content_competitors


def test_fairness_swap_brings_in_a_comparable_rival():
    items = [assessed(f"giant{index}", band="much_larger", matching=10 + index) for index in range(5)]
    items.append(assessed("peer", band="peer", matching=0, tier="medium"))
    proposal = SetProposer(SETTINGS).propose(uuid.uuid4(), items)
    assert proposal.fairness_swap and items[-1].channel_id in proposal.competitor_channel_ids


def test_creators_and_benchmarks_route_to_their_sets():
    creator = assessed(
        "drcreator", relation=CompetitorRelation.content, owner=OwnerKind.individual, query_hits=3
    )
    benchmark = assessed("mayo", relation=CompetitorRelation.benchmark)
    proposal = SetProposer(SETTINGS).propose(
        uuid.uuid4(), [creator, benchmark, *[assessed(f"d{i}") for i in range(3)]]
    )
    assert proposal.creator_seed_channel_ids == [creator.channel_id]
    assert proposal.benchmark_channel_ids == [benchmark.channel_id]
    assert creator.role is CandidateRole.creator_seed


def test_auto_confirm_needs_three_strong_corroborated_competitors():
    policy = AutoConfirmPolicy(SETTINGS)
    strong = [assessed(f"s{i}") for i in range(3)]
    proposal = SetProposer(SETTINGS).propose(uuid.uuid4(), strong)
    assert policy.allows(proposal, strong, set())
    assert not policy.allows(proposal, strong, {"llm"})
    weak = [assessed("w1"), assessed("w2"), assessed("w3", label=OwnershipLabel.likely, score=0.6)]
    proposal = SetProposer(SETTINGS).propose(uuid.uuid4(), weak)
    assert not policy.allows(proposal, weak, set())
    guessed = [assessed("g1"), assessed("g2"), assessed("g3", flags=("llm_only",))]
    assert not policy.allows(SetProposer(SETTINGS).propose(uuid.uuid4(), guessed), guessed, set())


def test_confidence_levels():
    verified = ChannelLead(
        "UCconfidenceChannel00001",
        "site_link",
        verdict=OwnershipVerdict(label=OwnershipLabel.verified, method="link", score=0.9, signals=[]),
    )
    high = BusinessRelevance(
        offering_overlap=1, geo_overlap=1, geo_basis="b", source_agreement=1, tier="high"
    )
    assert candidate_confidence(entity("x"), high, verified, thin_profile=False) == "high"
    assert candidate_confidence(entity("x"), high, verified, thin_profile=True) == "medium"
    assert candidate_confidence(entity("x", kinds=("llm",)), high, verified, thin_profile=False) == "medium"
    assert candidate_confidence(entity("x"), high, None, thin_profile=False) == "low"


IDS = [f"UC{str(index).rjust(22, 'a')}" for index in range(12)]


@pytest.mark.parametrize(
    ("selection", "code"),
    [
        (SetSelection(competitor_ids=IDS[:2]), "competitors_out_of_range"),
        (SetSelection(competitor_ids=IDS[:11]), "competitors_out_of_range"),
        (SetSelection(competitor_ids=IDS[:3], niche_ids=IDS[:1]), "channel_in_two_sets"),
        (SetSelection(competitor_ids=[*IDS[:2], "@handle"]), "channel_unresolved"),
        (SetSelection(competitor_ids=IDS[:3], own_ids=frozenset(IDS[:1])), "own_channel"),
        (SetSelection(competitor_ids=IDS[:3], uncertain_ids=frozenset(IDS[:1])), "uncertain_ownership"),
        (SetSelection(competitor_ids=IDS[:3], inactive_ids=frozenset(IDS[:1])), "too_few_active"),
    ],
)
def test_validator_rejections(selection, code):
    with pytest.raises(SetValidationError) as error:
        CompetitorSetValidator(SETTINGS).validate(selection)
    assert error.value.code == code


def test_validator_accepts_acknowledged_uncertain_and_extra_inactive():
    CompetitorSetValidator(SETTINGS).validate(
        SetSelection(
            competitor_ids=IDS[:4],
            uncertain_ids=frozenset(IDS[:1]),
            inactive_ids=frozenset(IDS[3:4]),
            acknowledge_uncertain=True,
        )
    )
