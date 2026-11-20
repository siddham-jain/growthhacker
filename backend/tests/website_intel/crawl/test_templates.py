import gzip

import pytest

from app.services.website_intel.crawl.templates import (
    Gazetteer,
    TemplateClusterer,
    apply_saturation_caps,
)
from app.services.website_intel.models import UrlCandidate

CITIES = ["hyderabad", "chennai", "delhi", "mumbai", "pune", "kolkata", "bangalore", "lucknow", "indore"]
SERVICES = [
    "angiogram",
    "knee-replacement",
    "cataract-surgery",
    "dialysis",
    "mri-scan",
    "ct-scan",
    "biopsy",
    "ecg",
]


@pytest.fixture(scope="module")
def clusterer() -> TemplateClusterer:
    return TemplateClusterer()


def test_gazetteer_knows_indian_cities() -> None:
    gazetteer = Gazetteer.load_default()
    assert gazetteer.contains("hyderabad") and gazetteer.contains("Bhubaneshwar")
    assert not gazetteer.contains("procedures")
    assert gazetteer.with_extra(["Jubilee Hills"]).contains("jubilee-hills")


def test_synthetic_city_by_service_grid(clusterer: TemplateClusterer) -> None:
    urls = [f"https://a.com/{city}/procedures/{service}" for city in CITIES for service in SERVICES]
    urls += ["https://a.com/about", "https://a.com/contact"]
    clustering = clusterer.cluster(urls)
    assert clustering.template_of[urls[0]] == "/{city}/procedures/{slug}"
    assert clustering.template_of["https://a.com/about"] == "/about"
    grid = next(
        summary for summary in clustering.summaries if summary.template_key == "/{city}/procedures/{slug}"
    )
    assert grid.count == len(CITIES) * len(SERVICES)
    assert set(grid.location_values) == set(CITIES)
    assert clustering.location_of[urls[0]] == "hyderabad"


def test_non_location_variable_position_uses_jaccard(clusterer: TemplateClusterer) -> None:
    brands = [f"brand{index}" for index in range(10)]
    urls = [
        f"https://shop.com/{brand}/{page}" for brand in brands for page in ("overview", "specs", "reviews")
    ]
    clustering = clusterer.cluster(urls)
    assert clustering.template_of[urls[0]] == "/{var}/overview"


def test_apollo_inventory_collapses(clusterer: TemplateClusterer, fixture_path) -> None:
    urls = gzip.decompress(fixture_path("crawl_apollo_sitemap_urls.txt.gz").read_bytes()).decode().split()
    clustering = clusterer.cluster(urls)
    top = {summary.template_key: summary for summary in clustering.summaries[:6]}
    assert "/{city}/procedures/{slug}" in top
    assert "/diseases-and-conditions/{slug}" in top
    assert top["/{city}/procedures/{slug}"].count > 4000
    assert {"hyderabad", "chennai", "delhi"} <= set(top["/{city}/procedures/{slug}"].location_values)
    assert len(clustering.summaries) < len(urls) / 10


def test_saturation_caps(clusterer: TemplateClusterer) -> None:
    urls = [f"https://a.com/{city}/procedures/{service}" for city in CITIES for service in SERVICES]
    urls.append("https://a.com/about")
    clustering = clusterer.cluster(urls)
    candidates = [UrlCandidate(url=url, source="sitemap") for url in urls]
    kept = apply_saturation_caps(candidates, per_template_cap=3, clustering=clustering)
    grid = [candidate for candidate in kept if "procedures" in candidate.url]
    assert len(grid) == 3
    assert len({clustering.location_of[candidate.url] for candidate in grid}) == 3
    assert kept[-1].url == "https://a.com/about"
    many = apply_saturation_caps(
        candidates, per_template_cap=50, clustering=clustering, max_location_values=4
    )
    assert len({clustering.location_of[c.url] for c in many if c.url in clustering.location_of}) == 4
