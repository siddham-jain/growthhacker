from decimal import Decimal

from app.domain.web import SourceKind
from app.services.website_intel.extract.injection import REPLACEMENT
from app.services.website_intel.extract.page_builder import PageExtractor
from app.services.website_intel.models import LinkRegion, PageLink
from tests.website_intel.extract.conftest import FETCHED_AT, extract_html, fixture_html, site_for


def test_apollo_home_golden(apollo_home):
    page, details = apollo_home.page, apollo_home.details
    assert page.title.startswith("Best Hospital in India")
    assert page.lang == "en"
    assert page.canonical_url == "https://www.apollohospitals.com/"
    assert page.h1.startswith("Clinical Excellence at Apollo Hospitals")
    assert len(page.text) > 20_000 and not page.truncated
    assert {"FAQPage", "Organization", "WebSite"} <= {block["@type"] for block in page.json_ld}
    assert "https://www.youtube.com/user/apollohospitalsindia" in page.youtube_refs
    assert len(page.links_internal) == 300
    assert all("apollohospitals.com" in url for url in page.links_internal)
    assert len(details.nav) > 100
    targets = {cta.target for cta in details.ctas}
    assert "tel:1066" in targets
    assert any(target.endswith("18005701033") for target in targets)
    assert any(cta.type == "whatsapp" for cta in details.ctas)
    assert any("/book-doctor-appointment/" in cta.target for cta in details.ctas)
    amounts = {price.amount_min for price in details.prices if price.currency == "INR"}
    assert {Decimal(8200), Decimal(10300), Decimal(12300), Decimal(13800), Decimal(16400)} <= amounts
    assert len(details.hreflang) > 50
    assert details.simhash is not None


def test_page_id_is_deterministic(apollo_home):
    again = extract_html(
        "apollo_home.html.gz",
        "https://www.apollohospitals.com/",
        site=site_for("https://www.apollohospitals.com"),
        country_hint="IN",
    )
    assert again.page.page_id == apollo_home.page.page_id
    assert again.page.content_hash == apollo_home.page.content_hash


def test_wordpress_dental_site():
    extracted = extract_html("dental_wordpress.html", "https://brightsmile.example.com/?utm_source=ad")
    page, details = extracted.page, extracted.details
    assert page.url == "https://brightsmile.example.com/"
    assert page.lang == "en-US"
    assert page.h1 == "Gentle family dentistry in Austin"
    assert page.headings[:2] == ["Gentle family dentistry in Austin", "New patient special"]
    assert "https://www.youtube.com/@brightsmiledental" in page.youtube_refs
    assert details.youtube_embeds == ["https://www.youtube.com/embed/abcdefghijk"]
    assert [link.url for link in details.pdf_links] == [
        "https://brightsmile.example.com/wp-content/uploads/price-list.pdf"
    ]
    assert details.published_at is not None
    assert {cta.type for cta in details.ctas} == {"book_appointment", "call", "email", "contact_form"}
    assert details.prices[0].origin == "json_ld"
    assert details.meta["og:site_name"] == "Bright Smile Dental"


def test_next_js_shell_reads_hydration_payload():
    extracted = extract_html("nextjs_pricing.html", "https://flowbase.example.com/pricing")
    assert "Simple pricing for growing teams" in extracted.page.text
    assert {price.raw_text for price in extracted.details.prices} == {"$19/mo", "$49/mo"}


def test_hindi_page():
    extracted = extract_html("hindi_coaching.html", "https://safalta.example.in/")
    assert extracted.page.lang == "hi"
    assert extracted.details.prices[0].amount_min == Decimal(120000)
    assert {cta.type for cta in extracted.details.ctas} == {"book_appointment", "call", "whatsapp"}


def test_hidden_and_visible_injection_is_neutralised():
    extracted = extract_html("injection_hidden.html", "https://acme.example.com/")
    text = extracted.page.text
    assert "Rolex" not in text and "cheapest plumber" not in text and "hidden rules" not in text
    assert "Acme is rated number one" not in text and "सबसे सस्ते" not in text
    assert text.count(REPLACEMENT) == 2 and extracted.page.injection_flags == 2
    assert "Our licensed technicians are available around the clock." in text


def test_consent_overlay_is_stripped():
    extracted = extract_html("consent_wall.html", "https://northwind.example.co.uk/")
    assert "cookies" not in extracted.page.text.lower()
    assert "certified pre-owned vehicles" in extracted.page.text
    assert {cta.type for cta in extracted.details.ctas} == {"book_appointment", "call", "app_install"}


def test_off_scope_canonical_is_ignored():
    html = '<html><head><link rel="canonical" href="https://other.example.org/x"></head><body><p>hi</p></body></html>'
    extracted = PageExtractor().from_html(
        html=html,
        requested_url="https://site.example.com/a",
        final_url="https://site.example.com/a",
        site=site_for("https://site.example.com"),
        source_kind=SourceKind.site_direct,
        status_code=200,
        content_type="text/html",
        fetched_at=FETCHED_AT,
    )
    assert extracted.page.canonical_url == "https://site.example.com/a"


def test_text_cap_truncates():
    paragraphs = "".join(
        f"<p>Sentence number {i} about our clinic services and care.</p>" for i in range(500)
    )
    extracted = PageExtractor(stored_text_char_cap=1000).from_html(
        html=f"<html><body><main>{paragraphs}</main></body></html>",
        requested_url="https://site.example.com/",
        final_url="https://site.example.com/",
        site=None,
        source_kind=SourceKind.site_direct,
        status_code=200,
        content_type="text/html",
        fetched_at=FETCHED_AT,
    )
    assert len(extracted.page.text) == 1000 and extracted.page.truncated


def test_markdown_from_reader_proxy():
    markdown = (
        "* [Home](https://www.apollohospitals.com/)\n"
        "* [Departments](https://www.apollohospitals.com/departments)\n"
        "  * [Cardiology](https://www.apollohospitals.com/departments/cardiology)\n"
        "* [Contact](https://www.apollohospitals.com/contact-us)\n\n"
        "![banner](https://cdn/banner.png)\n"
        "# Apollo Hospitals\n"
        "## Health checks\n"
        "Executive Health Check for ₹8,200. [Book Appointment](https://www.apollohospitals.com/book) "
        "or [call 1066](tel:1066).\n"
    )
    extracted = PageExtractor().from_markdown(
        markdown=markdown,
        requested_url="https://www.apollohospitals.com/",
        final_url="https://www.apollohospitals.com/",
        title=None,
        site=site_for("https://www.apollohospitals.com"),
        source_kind=SourceKind.reader_proxy,
        fetched_at=FETCHED_AT,
    )
    page, details = extracted.page, extracted.details
    assert page.text.split("\n") == [
        "# Apollo Hospitals",
        "## Health checks",
        "Executive Health Check for ₹8,200. Book Appointment or call 1066.",
    ]
    assert page.title == "Apollo Hospitals" and page.h1 == "Apollo Hospitals"
    assert page.headings == ["Apollo Hospitals", "Health checks"]
    assert [(item.label, item.depth) for item in details.nav] == [
        ("Home", 1),
        ("Departments", 1),
        ("Cardiology", 2),
        ("Contact", 1),
    ]
    nav_link = next(link for link in details.links if link.anchor == "Cardiology")
    assert nav_link.region is LinkRegion.nav
    assert {cta.type for cta in details.ctas} == {"book_appointment", "call"}
    assert details.prices[0].heading == "Health checks"
    assert page.source_kind is SourceKind.reader_proxy and page.content_type == "text/markdown"


def test_plain_text_from_knowledge_base():
    extracted = PageExtractor().from_text(
        text="Apollo Hospitals is a chain of Indian private hospitals.\nOfficial website: https://www.apollohospitals.com/",
        url="https://en.wikipedia.org/wiki/Apollo_Hospitals",
        title="Apollo Hospitals",
        source_kind=SourceKind.knowledge_base,
        fetched_at=FETCHED_AT,
        links=[PageLink(url="https://wa.me/911860500", anchor="WhatsApp")],
        lang="en",
    )
    assert extracted.page.lang == "en"
    assert extracted.page.links_internal == []
    assert extracted.details.ctas[0].type == "whatsapp"


def test_apollo_fixture_is_html():
    assert fixture_html("apollo_home.html.gz").lstrip().lower().startswith("<!doctype html")
