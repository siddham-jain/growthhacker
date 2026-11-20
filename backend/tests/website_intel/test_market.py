from decimal import Decimal

from app.domain.web import SiteRef
from app.services.website_intel.extract.cta import CtaExtractor, localise_calls
from app.services.website_intel.identity.knowledge import KbEntity
from app.services.website_intel.models import Hreflang, PageLink, PriceCandidate
from app.services.website_intel.profile.market import MarketInferrer, market_timezone
from tests.website_intel.conftest import cta, make_page

MEDANTA = "https://www.medanta.org/"


def site(domain: str) -> SiteRef:
    return SiteRef(
        input_url=domain,
        canonical_origin=f"https://www.{domain}",
        registrable_domain=domain,
        scope_hosts=[f"www.{domain}"],
    )


def infer(domain: str, pages, *, user: str | None = None, knowledge: KbEntity | None = None):
    return MarketInferrer().infer(user_country=user, site=site(domain), pages=pages, knowledge=knowledge)


def test_the_users_choice_wins() -> None:
    page = make_page(MEDANTA, "Call +91-88-0000-1068 or +91 739 295 7060")
    assert infer("medanta.org", [page], user="US").country == "US"


def test_country_tld_beats_other_signals_but_vanity_tlds_do_not_count() -> None:
    assert infer("clovedental.in", []).source == "cctld"
    assert infer("example.co.uk", []).country == "GB"
    assert infer("replit.ai", []).country is None


def test_medanta_org_is_india_from_its_phone_numbers() -> None:
    page = make_page(
        MEDANTA,
        "Emergency +91-88-0000-1068. International patients +91 739 295 7060. Toll free 1800 891 3100",
        links=[PageLink(url="https://api.whatsapp.com/send?phone=918800001068")],
    )
    result = infer("medanta.org", [page])
    assert (result.country, result.source) == ("IN", "phones")


def test_a_single_foreign_number_is_not_dominant() -> None:
    page = make_page(MEDANTA, "Call +91 88000 01068, +91 73929 57060 or our US desk +1 650 253 0000")
    assert infer("medanta.org", [page]).country == "IN"
    lone = make_page(MEDANTA, "Call +1 650 253 0000")
    assert infer("medanta.org", [lone]).country is None


def test_addresses_then_place_names_point_at_a_country() -> None:
    structured = make_page(
        MEDANTA,
        json_ld=[{"@type": "Hospital", "address": {"addressRegion": "Haryana", "addressCountry": "IN"}}],
    )
    assert infer("medanta.org", [structured]).source == "addresses"
    named = make_page(MEDANTA, "Hospitals in Gurugram, Lucknow, Patna and Indore, Madhya Pradesh")
    assert infer("narayanahealth.org", [named]).country == "IN"
    replit = make_page("https://replit.com/", "Replit, San Francisco, California. Offices in Palo Alto")
    assert infer("replit.com", [replit]).country == "US"


def test_currency_then_language_then_knowledge_base() -> None:
    rupees = make_page(MEDANTA, "Packages from ₹2,999 and ₹4,500")
    assert infer("medanta.org", [rupees]).source == "currency"
    dollars = make_page(
        "https://replit.com/pricing",
        prices=[
            PriceCandidate(
                raw_text="$25", amount_min=Decimal(25), currency="USD", page_url="https://replit.com/"
            ),
            PriceCandidate(
                raw_text="$40", amount_min=Decimal(40), currency="USD", page_url="https://replit.com/"
            ),
        ],
    )
    assert infer("replit.com", [dollars]).country == "US"
    tagged = make_page(MEDANTA, lang="en-IN")
    assert infer("medanta.org", [tagged]).source == "language"
    entity = KbEntity(qid="Q6805136", label="Medanta", countries=["India"])
    assert infer("medanta.org", [make_page(MEDANTA)], knowledge=entity).source == "knowledge_base"


def test_weak_evidence_leaves_the_market_unknown_not_us() -> None:
    result = infer("medanta.org", [make_page(MEDANTA, "World-class healthcare")])
    assert result.country is None and result.source is None


def test_hreflang_counts_only_for_the_page_itself() -> None:
    page = make_page(MEDANTA, lang="en")
    page.details.hreflang.extend(
        [Hreflang(lang="en-in", url=MEDANTA), Hreflang(lang="en-us", url="https://www.medanta.org/us/")]
    )
    assert infer("medanta.org", [page]).country == "IN"


def test_market_timezone_keeps_a_zone_inside_the_country() -> None:
    assert market_timezone("US", "America/Los_Angeles") == "America/Los_Angeles"
    assert market_timezone("IN", "Asia/Calcutta") == "Asia/Kolkata"
    assert market_timezone("IN", "America/New_York") == "Asia/Kolkata"


def test_a_crawl_without_a_market_keeps_national_numbers_for_later() -> None:
    found = CtaExtractor().extract(
        [PageLink(url="tel:1800 891 3100", anchor="1800 891 3100"), PageLink(url="tel:1066", anchor="Call")],
        page_url=MEDANTA,
    )
    assert [item.target for item in found] == ["tel:18008913100"]


def test_national_numbers_parse_in_the_inferred_market() -> None:
    pending = [
        cta("call", "tel:18008913100", MEDANTA, "1800 891 3100"),
        cta("call", "tel:01244141414", MEDANTA, "Call Now"),
        cta("call", "tel:+918800001068", MEDANTA, "+91-88-0000-1068"),
    ]
    localised = localise_calls(pending, "IN")
    assert [(item.target, item.label) for item in localised] == [
        ("tel:+9118008913100", "+91 1800 891 3100"),
        ("tel:+911244141414", "Call Now"),
        ("tel:+918800001068", "+91-88-0000-1068"),
    ]
    # with no market a national number cannot be dialled from the profile, so it is left out
    assert [item.target for item in localise_calls(pending, None)] == ["tel:+918800001068"]
