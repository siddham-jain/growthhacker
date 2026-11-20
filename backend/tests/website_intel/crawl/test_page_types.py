import pytest

from app.services.website_intel.crawl.page_types import PageTypeClassifier
from app.services.website_intel.crawl.templates import Gazetteer
from app.services.website_intel.models import PageType


@pytest.fixture(scope="module")
def classifier() -> PageTypeClassifier:
    return PageTypeClassifier(gazetteer=Gazetteer.load_default())


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://a.com/", PageType.home),
        ("https://a.com/about-us", PageType.about),
        ("https://a.com/departments", PageType.offering_hub),
        ("https://a.com/departments/cancer/precisiononcology", PageType.offering_detail),
        ("https://a.com/hyderabad/procedures/angiogram", PageType.offering_detail),
        ("https://a.com/pricing", PageType.pricing),
        ("https://a.com/health-checkup-packages", PageType.pricing),
        ("https://a.com/faqs", PageType.faq),
        ("https://a.com/patient-stories", PageType.case_study),
        ("https://a.com/doctors", PageType.team_hub),
        ("https://a.com/doctors/dr-rao", PageType.team_member),
        ("https://a.com/contact-us", PageType.contact),
        ("https://a.com/book-doctor-appointment/delhi", PageType.booking),
        ("https://a.com/blog", PageType.blog_index),
        ("https://a.com/blog/monsoon-tips", PageType.blog_post),
        ("https://a.com/diseases-and-conditions/asthma", PageType.knowledge_library),
        ("https://a.com/media-centre/press-releases", PageType.news_press),
        ("https://a.com/careers", PageType.careers),
        ("https://a.com/privacy-policy", PageType.legal),
        ("https://a.com/investor-relations", PageType.investor),
        ("https://a.com/locations", PageType.locations_hub),
        ("https://a.com/hospital/chennai", PageType.location_detail),
        ("https://a.com/hyderabad", PageType.location_detail),
        ("https://a.com/departments/neurology/treatment/about-brain-spine-surgery", PageType.offering_detail),
        ("https://a.com/%E0%A4%B8%E0%A4%82%E0%A4%AA%E0%A4%B0%E0%A5%8D%E0%A4%95", PageType.contact),
        ("https://a.com/hamare-bare-mein", PageType.about),
        ("https://a.com/xyz-123", PageType.other),
    ],
)
def test_url_rules(classifier: PageTypeClassifier, url: str, expected: PageType) -> None:
    assert classifier.classify(url) is expected


def test_json_ld_and_text_fallbacks(classifier: PageTypeClassifier) -> None:
    assert classifier.classify("https://a.com/p/123", json_ld_types=["FAQPage"]) is PageType.faq
    assert classifier.classify("https://a.com/p/124", anchor="Our Services") is PageType.offering_hub
    assert classifier.classify("https://a.com/p/125", h1="अपॉइंटमेंट बुक करें") is PageType.booking
    assert classifier.classify("https://a.com/p/126", title="Pricing | Acme") is PageType.pricing
    long_title = "The best hospital chain with doctors and services near you | Acme"
    assert classifier.classify("https://a.com/p/127", title=long_title) is PageType.other
    assert classifier.classify("https://a.com/x", is_home=True) is PageType.home


def test_url_beats_json_ld(classifier: PageTypeClassifier) -> None:
    assert (
        classifier.classify("https://a.com/services/implants", json_ld_types=["FAQPage"])
        is PageType.offering_detail
    )
