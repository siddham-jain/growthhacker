from selectolax.lexbor import LexborHTMLParser

from app.services.website_intel.extract.structured import (
    NavExtractor,
    embedded_app_state_text,
    extract_forms,
    extract_links,
    extract_meta,
    json_ld_blocks,
    published_at,
)
from app.services.website_intel.models import LinkRegion
from tests.website_intel.extract.conftest import fixture_html

BASE = "https://brightsmile.example.com/"


def test_json_ld_lenient_parse_flattens_graph_and_keeps_known_types():
    blocks = json_ld_blocks(fixture_html("dental_wordpress.html"))
    types = [block["@type"] for block in blocks]
    assert types == ["Dentist", "Product", "Physician"]
    assert blocks[0]["address"]["addressLocality"] == "Austin"
    assert blocks[2] == {"@type": "Physician"}


def test_json_ld_ignores_garbage_and_unknown_types():
    html = (
        '<script type="application/ld+json">{not json</script>'
        '<script type="application/ld+json">{"@type": "ImageObject"}</script>'
        '<script type="application/ld+json"><!-- [{"@type": ["Hospital", "Place"], "name": "X"}] --></script>'
    )
    assert json_ld_blocks(html) == [{"@type": ["Hospital", "Place"], "name": "X"}]


def test_meta_title_canonical_og_lang_and_published_time():
    meta = extract_meta(LexborHTMLParser(fixture_html("dental_wordpress.html")), BASE)
    assert meta.title == "Bright Smile Dental | Family Dentist in Austin"
    assert meta.canonical == BASE
    assert meta.lang == "en-US"
    assert meta.og["og:site_name"] == "Bright Smile Dental"
    assert meta.published_at is not None and meta.published_at.year == 2026


def test_hreflang_alternates(apollo_home):
    assert len(apollo_home.details.hreflang) > 50
    assert any(item.lang == "ar" for item in apollo_home.details.hreflang)


def test_published_at_falls_back_to_json_ld():
    found = published_at(None, [{"@type": "BlogPosting", "datePublished": "2026-10-01"}])
    assert found is not None and found.tzinfo is not None


def test_nav_taxonomy_depths():
    nav = NavExtractor().extract(LexborHTMLParser(fixture_html("dental_wordpress.html")), BASE)
    depths = {item.label: item.depth for item in nav}
    assert depths["Services"] == 1
    assert depths["Teeth Whitening"] == 2
    assert depths["Privacy"] == 1


def test_link_regions():
    links = extract_links(LexborHTMLParser(fixture_html("dental_wordpress.html")), BASE)
    regions = {link.anchor: link.region for link in links}
    assert regions["Services"] is LinkRegion.header
    assert regions["Email us"] is LinkRegion.footer
    assert regions["Download our price list"] is LinkRegion.body
    assert regions["Bright Smile Dental"] is LinkRegion.header


def test_forms_capture_action_fields_and_label():
    forms = extract_forms(LexborHTMLParser(fixture_html("dental_wordpress.html")), BASE)
    assert forms[0].action == "https://brightsmile.example.com/contact/"
    assert "your-email" in forms[0].fields
    assert forms[0].submit_label == "Send enquiry"


def test_next_data_payload_yields_page_text():
    text = embedded_app_state_text(fixture_html("nextjs_pricing.html"))
    assert text is not None
    lines = text.split("\n")
    assert "Starter" in lines and "$49/mo" in lines
    assert "Can I cancel my subscription at any time?" in lines
    assert "/images/hero.png" not in text and "abc123" not in text


def test_nuxt_payload_strings():
    html = (
        '<script>window.__NUXT__={data:[{intro:"We build custom kitchens across Leeds and York."}]}</script>'
    )
    assert embedded_app_state_text(html) == "We build custom kitchens across Leeds and York."


def test_no_app_state_returns_none():
    assert embedded_app_state_text("<html><body><p>plain</p></body></html>") is None


def test_link_labels_skip_inline_svg_styles():
    html = (
        "<body><a href='tel:+919972899728'><svg><style>.st0 {fill: none; stroke-width: 40px;}</style>"
        "<path d='M12 2C6.48 2 2 6.48 2 12'/></svg>+91 99728-99728</a>"
        "<a href='/contact' aria-label='Contact us'>.cls-1 {fill: none;}</a></body>"
    )
    labels = {link.url: link.anchor for link in extract_links(LexborHTMLParser(html), BASE)}
    assert labels == {"tel:+919972899728": "+91 99728-99728", BASE + "contact": "Contact us"}
