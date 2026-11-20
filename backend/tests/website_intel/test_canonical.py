from typing import Any

import pytest

from app.domain.profile import (
    ClaimMeta,
    ClaimOrigin,
    ClaimStatus,
    ConversionAction,
    Evidence,
    Location,
    Offering,
    Prominence,
    ValueProposition,
)
from app.domain.web import ExtractionKind, SourceKind
from app.services.website_intel.profile.canonical import (
    brand_tokens,
    canonical_tokens,
    merge_conversions,
    merge_locations,
    merge_offerings,
    merge_value_props,
)
from app.services.website_intel.profile.claims import stable_id
from app.services.website_intel.profile.non_offerings import business_words, cta_type, non_offering_reason
from app.services.website_intel.profile.places import foreign_mentions, physical_sites, stated_country
from app.services.website_intel.profile.validators import ValidatorChain
from tests.website_intel.conftest import NOW
from tests.website_intel.test_versioning import profile as versioned_profile

SITE = "https://www.medanta.org"
BRANDS = brand_tokens(["Medanta"])


def meta(quote: str, path: str = "/") -> ClaimMeta:
    return ClaimMeta(
        origin=ClaimOrigin.extracted,
        status=ClaimStatus.proposed,
        confidence=0.6,
        evidence=[
            Evidence(
                source_kind=SourceKind.site_direct,
                extraction=ExtractionKind.page_text,
                source_url=SITE + path,
                quote=quote,
                match_score=100,
                fetched_at=NOW,
            )
        ],
        first_seen_version=1,
        updated_at=NOW,
    )


def location(name: str, city: str | None = None, region: str | None = None, kind: str = "branch") -> Location:
    return Location(
        id=stable_id("loc", city, name),
        name=name,
        kind=kind,  # type: ignore[arg-type]
        city=city,
        region=region,
        meta=meta(name, "/" + stable_id("p", name)),
    )


def offering(name: str, url: str | None = None, **extra: Any) -> Offering:
    return Offering(id=stable_id("off", name), name=name, kind="service", url=url, meta=meta(name), **extra)


def action(type_: str, target: str, label: str) -> ConversionAction:
    return ConversionAction(
        id=stable_id("cta", type_, target),
        type=type_,  # type: ignore[arg-type]
        label=label,
        target=target,
        medium="web",
        prominence=Prominence(pages_seen=1, on_home=False, in_header=False, in_footer=False),
        meta=meta(label),
    )


def test_canonical_tokens_unify_dashes_spaces_and_spelling() -> None:
    assert canonical_tokens("Medanta - The Medicity") == canonical_tokens("Medanta–The Medicity")
    assert canonical_tokens("Super Speciality Hospital") == canonical_tokens("Super Specialty Hospital")
    assert canonical_tokens("Lab & Pathology") == ("lab", "and", "pathology")
    assert canonical_tokens("Paediatric Care") == ("pediatric", "care")


def test_brand_tokens_take_the_leading_distinctive_word() -> None:
    assert brand_tokens(["Medanta"]) == {"medanta"}
    assert brand_tokens(["Apollo Hospitals", "The Narayana Health"]) == {"apollo", "narayana"}


def test_spelling_and_dash_variants_of_one_facility_merge_with_every_quote() -> None:
    items = [
        location("Medanta Super Specialty Hospital, Indore", "Indore", kind="hospital"),
        location("Medanta Super Speciality Hospital, Indore", "Madhya Pradesh"),
        location("Medanta - The Medicity, Gurugram", "Gurugram", kind="hospital"),
        location("Medanta–The Medicity, Gurugram", "Haryana"),
    ]
    merged = merge_locations(items, BRANDS)
    assert [item.name for item in merged] == [
        "Medanta Super Specialty Hospital, Indore",
        "Medanta - The Medicity, Gurugram",
    ]
    indore, gurugram = merged
    assert (indore.city, indore.region) == ("Indore", "Madhya Pradesh")
    assert (gurugram.city, gurugram.region) == ("Gurugram", "Haryana")
    quotes = {evidence.quote for item in merged for evidence in item.meta.evidence}
    assert quotes == {item.name for item in items}


def test_sentences_that_only_name_a_city_fold_into_that_citys_facility() -> None:
    items = [
        location("Jay Prabha Medanta Super Specialty Hospital, Patna", "Patna", "Bihar", kind="hospital"),
        location("Medanta Clinic at Sri Harmandir Ji Patna Sahib", "Patna", kind="clinic"),
        location("Medanta Super Speciality Hospital, Lucknow", "Lucknow", kind="hospital"),
        location("Super speciality hospital in Patna"),
        location("Located in Patna, Bihar"),
        location("Medanta has operations in Lucknow"),
        location("Located in Lucknow, Uttar Pradesh"),
        location("Clinic location at Sri Harmandir Ji Patna Sahib"),
    ]
    merged = {item.name: item for item in merge_locations(items, BRANDS)}
    assert set(merged) == {
        "Jay Prabha Medanta Super Specialty Hospital, Patna",
        "Medanta Clinic at Sri Harmandir Ji Patna Sahib",
        "Medanta Super Speciality Hospital, Lucknow",
    }
    patna = merged["Jay Prabha Medanta Super Specialty Hospital, Patna"]
    assert {e.quote for e in patna.meta.evidence} == {
        "Jay Prabha Medanta Super Specialty Hospital, Patna",
        "Super speciality hospital in Patna",
        "Located in Patna, Bihar",
    }
    assert len(merged["Medanta Super Speciality Hospital, Lucknow"].meta.evidence) == 3
    assert len(merged["Medanta Clinic at Sri Harmandir Ji Patna Sahib"].meta.evidence) == 2


def test_different_clinics_in_one_city_stay_separate() -> None:
    items = [
        location("Medanta - The Medicity, Gurugram", "Gurugram", kind="hospital"),
        location("Medanta Mediclinic at Subhash Chowk", "Gurugram", kind="clinic"),
        location("Medanta Mediclinic at DLF Cybercity", "Gurugram", kind="clinic"),
        location("Medanta Mediclinic at Subhash Chowk Gurugram"),
        location("Medanta Mediclinic at Ranchi", "Ranchi", kind="clinic"),
        location("Medanta Mediclinic at Guwahati", "Guwahati", kind="clinic"),
        location("Medanta", "Gurugram"),
    ]
    merged = merge_locations(items, BRANDS)
    assert [item.name for item in merged] == [
        "Medanta - The Medicity, Gurugram",
        "Medanta Mediclinic at Subhash Chowk",
        "Medanta Mediclinic at DLF Cybercity",
        "Medanta Mediclinic at Ranchi",
        "Medanta Mediclinic at Guwahati",
    ]
    assert merged[1].aliases == ["Medanta Mediclinic at Subhash Chowk Gurugram"]
    # the bare "Medanta, Gurugram" address belongs to the city's hospital, not a clinic
    assert len(merged[0].meta.evidence) == 2


def test_an_address_number_does_not_make_a_new_clinic() -> None:
    merged = merge_locations(
        [
            location("Medanta Mediclinic at Defence Colony", "New Delhi", kind="clinic"),
            location("Medanta Mediclinic at E-15 Defence Colony New Delhi"),
        ],
        BRANDS,
    )
    assert len(merged) == 1 and merged[0].city == "New Delhi"


def test_city_only_facts_without_a_facility_become_one_entry_per_city() -> None:
    merged = merge_locations(
        [
            location("Located in Noida, Uttar Pradesh", "Noida"),
            location("Medanta has operations in Noida", "Noida"),
        ],
        BRANDS,
    )
    assert len(merged) == 1 and len(merged[0].meta.evidence) == 2


def test_offering_variants_merge_and_distinct_offerings_stay() -> None:
    items = [
        offering("Paediatric Care", SITE + "/paediatric-care", is_primary=True),
        offering("Pediatric care"),
        offering("Liver Transplant"),
        offering("Lung Transplant"),
        offering("Cardiac Care"),
        offering("Cardiac Health Package"),
        offering("Gastrosciences"),
        offering("Gastrosiences"),
        offering("Medanta Years to Life Health Checkup (Women)"),
        offering("Medanta Years to Life Health Checkup (Men)"),
    ]
    merged, remap = merge_offerings(items, BRANDS)
    assert [item.name for item in merged] == [
        "Paediatric Care",
        "Liver Transplant",
        "Lung Transplant",
        "Cardiac Care",
        "Cardiac Health Package",
        "Gastrosciences",
        "Medanta Years to Life Health Checkup (Women)",
        "Medanta Years to Life Health Checkup (Men)",
    ]
    assert merged[5].aliases == ["Gastrosiences"]
    # a spelling variant compares equal, so it needs no alias
    assert merged[0].aliases == [] and merged[0].url == SITE + "/paediatric-care"
    assert len(merged[0].meta.evidence) == 2
    assert remap == {items[1].id: items[0].id, items[7].id: items[6].id}


def test_value_props_and_conversions_dedupe_without_losing_quotes() -> None:
    props = [
        ValueProposition(
            id="vp_1", statement="Accredited by JCI and NABH", kind="credential", meta=meta("JCI")
        ),
        ValueProposition(
            id="vp_2", statement="Accredited by NABH and JCI.", kind="credential", meta=meta("NABH")
        ),
        ValueProposition(id="vp_3", statement="1440 beds", kind="scale", meta=meta("1440 beds")),
        ValueProposition(id="vp_4", statement="1400 beds", kind="scale", meta=meta("1400 beds")),
    ]
    kept = merge_value_props(props)
    assert [item.id for item in kept] == ["vp_1", "vp_3", "vp_4"]
    assert {e.quote for e in kept[0].meta.evidence} == {"JCI", "NABH"}
    actions = merge_conversions(
        [
            action("contact_form", "https://www.medanta.org/contact-us", "Contact Us"),
            action("contact_form", "https://medanta.org/contact-us/?utm_source=x", "Contact"),
            action("book_appointment", "https://care.medanta.org/landing", "Book Appointment"),
            action("book_appointment", "https://www.medanta.org/doctor/dr-nitin-sood", "Book Appointment"),
            action("visit_location", "https://maps.app.goo.gl/HZVm5hgafAgZYFQ97", "Get Directions"),
            action("visit_location", "https://www.google.com/maps/dir/Medanta", "Get Directions"),
            action("book_appointment", "https://care.medanta.org/explore/labtest", "Book a Test"),
        ]
    )
    assert [item.label for item in actions] == [
        "Contact Us",
        "Book Appointment",
        "Get Directions",
        "Book a Test",
    ]
    assert actions[1].target == "https://care.medanta.org/landing" and len(actions[1].meta.evidence) == 1
    assert len(actions[0].meta.evidence) == 2


@pytest.mark.parametrize(
    ("name", "url", "reason"),
    [
        ("Request An Estimate", None, "call_to_action"),
        ("Book Appointment", None, "call_to_action"),
        ("Find a Doctor", None, "call_to_action"),
        ("Download from App Store", None, "app_store"),
        ("Medanta App", "https://apps.apple.com/in/app/medanta/id6741807921", "app_store"),
        ("Get it on Google Play", None, "app_store"),
        ("Treatments", None, "navigation_label"),
        ("Our Specialities", None, "navigation_label"),
        ("Centres of Excellence", None, "navigation_label"),
        ("Medanta Services", None, "navigation_label"),
        ("Cardiac Care", None, None),
        ("Health Checkup", None, None),
        ("International Patient Services", None, None),
        ("Bone Health Package", None, None),
        ("Emergency 1068", None, None),
        ("Second Opinion", None, None),
    ],
)
def test_non_offering_labels(name: str, url: str | None, reason: str | None) -> None:
    assert non_offering_reason(name, url, BRANDS) == reason


def test_request_an_estimate_reads_as_a_quote_request() -> None:
    assert cta_type("Request An Estimate") == "quote_request"


def test_validator_drops_non_offerings_and_unlinks_their_children() -> None:
    parent = offering("Treatments")
    child = offering("TAVI", parent_id=parent.id)
    estimate = offering("Request An Estimate", SITE + "/request-an-estimate")
    book = action("book_appointment", "https://care.medanta.org/landing", "Book Appointment").model_copy(
        update={"offering_ids": [parent.id, child.id]}
    )
    cleaned, issues = ValidatorChain().apply(
        versioned_profile(1, [parent, child, estimate], conversion_actions=[book])
    )
    assert [item.name for item in cleaned.offerings] == ["TAVI"]
    assert cleaned.offerings[0].parent_id is None
    assert cleaned.conversion_actions[0].offering_ids == [child.id]
    assert {issue.code for issue in issues} == {"navigation_label", "call_to_action"}


def sourced(
    item: Location, source: SourceKind, extraction: ExtractionKind = ExtractionKind.page_text
) -> Location:
    evidence = [
        entry.model_copy(update={"source_kind": source, "extraction": extraction})
        for entry in item.meta.evidence
    ]
    return item.model_copy(update={"meta": item.meta.model_copy(update={"evidence": evidence})})


def test_third_party_locations_in_another_country_are_a_different_company() -> None:
    bienne = sourced(location("Headquarters in Bienne, Switzerland"), SourceKind.web_mentions)
    punjab = sourced(location("Hoshiarpur office", "Hoshiarpur", "Punjab"), SourceKind.web_mentions)
    own = location("Headquarters in Bienne, Switzerland")
    kept = foreign_mentions([bienne, punjab, own], "IN")
    assert kept == [punjab, own]
    assert foreign_mentions([bienne], None) == [bienne]


def test_only_physical_sites_and_stated_presence_are_locations() -> None:
    office = location("LeRoy Print Packs", "Hoshiarpur", "Punjab", kind="office").model_copy(
        update={"address": "Jalandhar Road, Kurangana Link Road, Hoshiarpur"}
    )
    items = [
        office,
        location("Dispatch anywhere in India", kind="service_area"),
        location("Dispatch to anywhere in India"),
        location("Bihar", "Bihar"),
        sourced(location("Mumbai", "Mumbai"), SourceKind.site_direct, ExtractionKind.url_inventory),
        location("Ludhiana branch", "Ludhiana", kind="branch"),
    ]
    kept, reach = physical_sites(items, business_name="LeRoy Print Packs")
    assert [(item.name, item.kind) for item in kept] == [
        ("Hoshiarpur, Punjab", "office"),
        ("Ludhiana branch", "branch"),
    ]
    assert kept[0].aliases == ["LeRoy Print Packs"]
    assert [item.statement for item in merge_value_props(reach)] == ["Dispatch anywhere in India"]
    assert all(item.kind == "accessibility" for item in reach)


def test_stated_country_reads_names_not_pronouns() -> None:
    assert stated_country(location("Headquarters in Bienne, Switzerland")) == "CH"
    assert stated_country(location("Visit us at our plant")) is None
    assert stated_country(location("Plant in Hoshiarpur", region="Punjab")) is None


@pytest.mark.parametrize(
    ("name", "reason"),
    [
        ("Products", "navigation_label"),
        ("Packaging by Use Case", "category_label"),
        ("Pan-India Packaging", "category_label"),
        ("Packaging Solutions", "category_label"),
        ("Folding cartons", None),
        ("Corrugated boxes", None),
        ("Offset Printing", None),
    ],
)
def test_category_labels_of_a_packaging_business(name: str, reason: str | None) -> None:
    niche = business_words("Offset printing, cartons and corrugated packaging manufacturer in Punjab")
    assert non_offering_reason(name, None, brand_tokens(["LeRoy Print Packs"]), niche) == reason
