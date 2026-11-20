import uuid

import pytest

from app.domain.competitors import CandidateRole, CandidateSource, CandidateStatus, CompetitorRelation, GeoRef
from app.domain.youtube import OwnershipLabel, OwnershipVerdict
from app.services.competitors.identity.merger import EntityMerger, suppress_rejected
from app.services.competitors.identity.normalize import (
    distinctive_tokens,
    normalize_name,
    registrable_domain,
    token_set_ratio,
)
from app.services.competitors.identity.self_filter import SelfIdentityFilter
from app.services.competitors.model import ChannelLead, KnownCandidate, RawCandidate
from tests.competitors.apollo import apollo_profile
from tests.competitors.fakes import NOW


def raw(name, kind="web_search", domain=None, relation="unknown", country=None, channel_id=None):
    return RawCandidate(
        name=name,
        domain=domain,
        relation_guess=relation,
        geography=[GeoRef(country_iso2=country)] if country else [],
        channel_id=channel_id,
        source=CandidateSource(kind=kind, ref="ref", retrieved_at=NOW),
    )


@pytest.mark.parametrize(
    ("name", "normalized"),
    [
        ("Fortis Healthcare Ltd.", "fortis healthcare"),
        ("Max Healthcare Institute Limited", "max healthcare institute"),
        ("Apollo 24|7", "apollo 24 7"),
        ("Johnson & Johnson Pvt Ltd", "johnson and johnson"),
        ("Group", "group"),
        ("नारायणा हेल्थ", "नारायणा हेल्थ"),
    ],
)
def test_normalize_name(name, normalized):
    assert normalize_name(name) == normalized


def test_registrable_domain_and_tokens():
    assert registrable_domain("https://www.apollohospitals.co.in/x") == "apollohospitals.co.in"
    assert registrable_domain("my.clevelandclinic.org") == "clevelandclinic.org"
    assert registrable_domain("unknown") is None
    assert distinctive_tokens("Apollo Hospitals") == {"apollo"}
    assert distinctive_tokens("Hospitals") == {"hospitals"}


def test_token_set_ratio_handles_subsets_and_differences():
    assert token_set_ratio("medanta", "medanta the medicity") == 100
    assert token_set_ratio("apollo hospitals", "apollo tyres") < 92
    assert token_set_ratio("", "x") == 0


def test_merge_by_domain_name_and_channel():
    merger = EntityMerger(92)
    entities = merger.merge(
        [
            raw("Narayana Health", "llm", "narayanahealth.org", "direct", "IN"),
            raw("Narayana Hrudayalaya", "web_search", "https://www.narayanahealth.org"),
            raw("Medanta", "llm", "medanta.org"),
            raw("Medanta - The Medicity", "web_search"),
            raw("Max Healthcare", "llm", country="IN"),
            raw("Max Healthcare", "web_search", country="US"),
            raw("Practo", "niche_search", channel_id="UCGlsl2YPuCvklhbUWu1vuqQ"),
            raw("Practo Technologies", "user", channel_id="UCGlsl2YPuCvklhbUWu1vuqQ"),
        ],
        [],
    )
    names = sorted(entity.brand_name for entity in entities)
    assert names == [
        "Max Healthcare",
        "Max Healthcare",
        "Medanta - The Medicity",
        "Narayana Hrudayalaya",
        "Practo Technologies",
    ]
    narayana = next(entity for entity in entities if entity.official_domain == "narayanahealth.org")
    assert narayana.source_kinds == {"llm", "web_search"} and "llm_only" not in narayana.flags
    assert "Narayana Health" in narayana.aliases


def test_llm_domain_stays_unverified_until_another_source_names_it():
    merger = EntityMerger(92)
    [entity] = merger.merge([raw("Zenith", "llm", "zenith.in")], [])
    assert entity.flags >= {"llm_only", "domain_unverified"}
    [entity] = merger.merge(
        [raw("Zenith", "llm", "zenith.in"), raw("Zenith", "website_hint", "zenith.in")], []
    )
    assert "domain_unverified" not in entity.flags and "llm_only" not in entity.flags


def test_second_pass_merges_entities_on_the_same_channel():
    merger = EntityMerger(92)
    first, second = merger.merge(
        [raw("Fortis", "llm", "fortis.com"), raw("Fortis Escorts", "web_search", "fortisescorts.in")], []
    )
    verdict = OwnershipVerdict(label=OwnershipLabel.verified, method="link", score=0.9, signals=[])
    first.leads["UCn0ShMNqXI3kQP-Eoaf7olg"] = ChannelLead(
        "UCn0ShMNqXI3kQP-Eoaf7olg", "site_link", verdict=verdict
    )
    second.leads["UCn0ShMNqXI3kQP-Eoaf7olg"] = ChannelLead(
        "UCn0ShMNqXI3kQP-Eoaf7olg", "site_link", verdict=verdict
    )
    [merged] = merger.merge_by_channel([first, second])
    assert merged.source_kinds == {"llm", "web_search"} and "Fortis Escorts" in merged.aliases


def test_known_rejections_are_suppressed_and_tracked_entities_carried_over():
    rejected = KnownCandidate(
        id=uuid.uuid4(),
        brand_name="Max Healthcare",
        normalized_name="max healthcare",
        official_domain="maxhealthcare.in",
        aliases=[],
        channel_ids=[],
        status=CandidateStatus.rejected,
        role=CandidateRole.excluded,
        relation=CompetitorRelation.direct,
        decided_by="user",
        first_seen_run_id=None,
        rejection_reason="too big",
    )
    tracked = KnownCandidate(
        id=uuid.uuid4(),
        brand_name="Medanta",
        normalized_name="medanta",
        official_domain="medanta.org",
        aliases=[],
        channel_ids=["UCSxY3aIGBg47WJASNYH6mUA"],
        status=CandidateStatus.confirmed,
        role=CandidateRole.competitor_set,
        relation=CompetitorRelation.direct,
        decided_by="user",
        first_seen_run_id=None,
    )
    entities = EntityMerger(92).merge([raw("Max Healthcare", "llm", "maxhealthcare.in")], [rejected, tracked])
    suppress_rejected(entities)
    max_entity, carried = entities
    assert max_entity.status is CandidateStatus.rejected and max_entity.rejection_reason == "too big"
    assert "carried_over" in carried.flags and carried.lead_refs[0].value == "UCSxY3aIGBg47WJASNYH6mUA"


@pytest.mark.parametrize(
    ("name", "domain", "guess", "relation"),
    [
        ("Apollo Hospitals", None, "unknown", CompetitorRelation.self_),
        ("Apollo 24|7", None, "unknown", CompetitorRelation.subsidiary),
        ("Apollo 247 app", "apollo247.com", "unknown", CompetitorRelation.subsidiary),
        ("Apollo Pharmacy", None, "sister", CompetitorRelation.subsidiary),
        ("Apollo Cancer Centres", None, "subsidiary", CompetitorRelation.subsidiary),
        ("Apollo Group Holdings", None, "parent", CompetitorRelation.parent),
        ("Apollo Tyres", None, "unknown", None),
        ("Fortis Healthcare", None, "sister", None),
        ("Anything", "apollohospitals.com", "unknown", CompetitorRelation.self_),
    ],
)
def test_self_filter(name, domain, guess, relation):
    [entity] = EntityMerger(92).merge([raw(name, "llm", domain, guess)], [])
    flt = SelfIdentityFilter(apollo_profile(), ["UCownChannel0000000000aa"], {"apollohospitals.com"})
    assert flt.classify(entity) == relation


def test_self_filter_catches_own_channel():
    [entity] = EntityMerger(92).merge(
        [raw("Some Channel", "niche_search", channel_id="UCownChannel0000000000aa")], []
    )
    flt = SelfIdentityFilter(apollo_profile(), ["UCownChannel0000000000aa"], {"apollohospitals.com"})
    assert flt.apply([entity]) == [entity] and entity.status is CandidateStatus.excluded
