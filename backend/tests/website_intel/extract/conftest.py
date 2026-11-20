import gzip
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.domain.web import SiteRef, SourceKind
from app.services.website_intel.extract.page_builder import PageExtractor
from app.services.website_intel.models import ExtractedPage

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
FETCHED_AT = datetime(2026, 11, 20, 8, 0, tzinfo=UTC)


def fixture_html(name: str) -> str:
    path = FIXTURES / f"extract_{name}"
    if path.suffix == ".gz":
        return gzip.decompress(path.read_bytes()).decode("utf-8")
    return path.read_text(encoding="utf-8")


def site_for(origin: str) -> SiteRef:
    host = origin.split("://", 1)[1]
    bare = host.removeprefix("www.")
    return SiteRef(
        input_url=bare,
        canonical_origin=origin,
        registrable_domain=bare,
        scope_hosts=[host, bare] if host != bare else [host],
    )


def extract_html(
    name: str, url: str, *, site: SiteRef | None = None, country_hint: str | None = None
) -> ExtractedPage:
    return PageExtractor().from_html(
        html=fixture_html(name),
        requested_url=url,
        final_url=url,
        site=site,
        source_kind=SourceKind.site_direct,
        status_code=200,
        content_type="text/html",
        fetched_at=FETCHED_AT,
        country_hint=country_hint,
    )


@pytest.fixture(scope="session")
def apollo_home() -> ExtractedPage:
    return extract_html(
        "apollo_home.html.gz",
        "https://www.apollohospitals.com/",
        site=site_for("https://www.apollohospitals.com"),
        country_hint="IN",
    )
