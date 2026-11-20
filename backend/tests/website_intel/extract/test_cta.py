import pytest

from app.services.website_intel.extract.cta import CtaExtractor, FormInfo, markdown_links, normalize_phone
from app.services.website_intel.models import LinkRegion, PageLink

PAGE = "https://clinic.example.in/"


def _one(link: PageLink, country_hint: str | None = "IN"):
    found = CtaExtractor().extract([link], page_url=PAGE, country_hint=country_hint)
    return found[0] if found else None


def test_tel_links_normalise_to_e164():
    cta = _one(PageLink(url="tel:040 4344 1066", anchor="Call", region=LinkRegion.header))
    assert cta is not None and cta.target == "tel:+914043441066"
    assert cta.type == "call" and cta.medium == "phone" and cta.in_header


def test_short_codes_keep_their_digits():
    assert normalize_phone("1066", "IN") == "1066"


def test_region_falls_back_to_country_tld():
    found = CtaExtractor().extract(
        [PageLink(url="tel:020 7946 0018")], page_url="https://shop.example.co.uk/"
    )
    assert found[0].target == "tel:+442079460018"


@pytest.mark.parametrize(
    ("url", "anchor", "expected_type", "medium"),
    [
        ("https://wa.me/919876543210", "", "whatsapp", "whatsapp"),
        ("https://api.whatsapp.com/send?phone=91987", "Chat", "whatsapp", "whatsapp"),
        ("mailto:Info@Clinic.example.in?subject=hi", "Mail", "email", "email"),
        ("https://apps.apple.com/in/app/x/id1", "iOS", "app_install", "app"),
        ("https://play.google.com/store/apps/details?id=x", "Android", "app_install", "app"),
        ("https://calendly.com/clinic/consult", "Talk to us", "book_appointment", "web"),
        ("https://calendly.com/saas/intro", "Book a demo", "demo", "web"),
        ("https://clinic.example.in/x", "Book an Appointment", "book_appointment", "web"),
        ("https://clinic.example.in/x", "Request a demo", "demo", "web"),
        ("https://clinic.example.in/x", "Start your free trial", "free_trial", "web"),
        ("https://clinic.example.in/x", "Get started", "signup", "web"),
        ("https://clinic.example.in/x", "Add to cart", "buy", "web"),
        ("https://clinic.example.in/x", "Get a quote", "quote_request", "web"),
        ("https://clinic.example.in/x", "Request a call back", "contact_form", "web"),
        ("https://clinic.example.in/x", "Download our brochure", "download", "web"),
        ("https://clinic.example.in/x", "Find a hospital", "visit_location", "web"),
        ("https://clinic.example.in/x", "अपॉइंटमेंट बुक करें", "book_appointment", "web"),
        ("https://clinic.example.in/x", "अभी कॉल करें", "call", "web"),
        ("https://clinic.example.in/x", "Appointment book karein", "book_appointment", "web"),
        ("https://clinic.example.in/x", "Abhi call karein", "call", "web"),
        (
            "https://clinic.example.in/book-doctor-appointment/cardiologist",
            "Cardiology",
            "book_appointment",
            "web",
        ),
    ],
)
def test_link_classification(url, anchor, expected_type, medium):
    cta = _one(PageLink(url=url, anchor=anchor))
    assert cta is not None
    assert (cta.type, cta.medium) == (expected_type, medium)


def test_plain_links_are_not_ctas():
    assert _one(PageLink(url="https://clinic.example.in/about", anchor="About us")) is None
    assert _one(PageLink(url="javascript:void(0)", anchor="Book now")) is None


def test_mailto_target_is_lowercased_without_query():
    cta = _one(PageLink(url="mailto:Info@Clinic.example.in?subject=hi", anchor="Mail"))
    assert cta is not None and cta.target == "mailto:info@clinic.example.in"


def test_duplicates_merge_prominence_flags():
    links = [
        PageLink(url="tel:+914043441066", anchor="Call", region=LinkRegion.header),
        PageLink(url="tel:+91 4043441066", anchor="", region=LinkRegion.footer),
    ]
    found = CtaExtractor().extract(links, page_url=PAGE)
    assert len(found) == 1 and found[0].in_header and found[0].in_footer


def test_forms_are_classified_by_fields():
    forms = [
        FormInfo(action="https://clinic.example.in/book", fields=("name", "phone", "appointment-date")),
        FormInfo(
            action="https://clinic.example.in/contact",
            fields=("your-name", "email", "message"),
            submit_label="Send",
        ),
        FormInfo(action="https://clinic.example.in/news", fields=("email",)),
        FormInfo(action="https://clinic.example.in/login", fields=("username", "password")),
        FormInfo(action="https://clinic.example.in/search", fields=("search",)),
    ]
    found = CtaExtractor().extract([], page_url=PAGE, forms=forms)
    assert [cta.type for cta in found] == ["book_appointment", "contact_form", "newsletter"]


def test_markdown_links_handle_images_and_autolinks():
    markdown = '[![Logo](https://x/logo.png)](https://x/) see [Book now](https://x/book "t") <tel:+911234>'
    links = markdown_links(markdown)
    assert [(link.url, link.anchor) for link in links] == [
        ("https://x/", "Logo"),
        ("https://x/book", "Book now"),
        ("tel:+911234", ""),
    ]
