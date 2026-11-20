import pytest

from app.ports.crawler import FetchContext
from app.services.website_intel.inputs.site_resolver import SiteResolver, looks_parked
from app.services.website_intel.inputs.ssrf import (
    DnsLookupError,
    SsrfGuard,
    UnsafeTargetError,
    is_public_address,
)
from app.services.website_intel.inputs.urls import (
    InvalidUrlError,
    dedupe_key,
    normalize_url,
    registrable_domain,
)
from tests.website_intel.conftest import FakeRawFetcher, Served, html_page

CTX = FetchContext(client_id=None, run_id=None)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("apollohospitals.com", "https://apollohospitals.com/"),
        (
            "HTTP://WWW.Example.com:80/a//b/../c?utm_source=x&b=2&a=1#frag",
            "http://www.example.com/a/c?a=1&b=2",
        ),
        ("https://example.com:443/x?gclid=1&fbclid=2", "https://example.com/x"),
        ("https://bücher.de/", "https://xn--bcher-kva.de/"),
        ("https://example.com/hyderabad/", "https://example.com/hyderabad/"),
        ("example.com/%7Euser", "https://example.com/~user"),
    ],
)
def test_normalize_url(raw: str, expected: str) -> None:
    assert normalize_url(raw) == expected


@pytest.mark.parametrize("raw", ["", "ftp://example.com", "javascript:alert(1)", "exa mple.com", "x" * 2100])
def test_rejects_unusable_input(raw: str) -> None:
    with pytest.raises(InvalidUrlError) as error:
        normalize_url(raw)
    assert "website address" in error.value.user_message


def test_registrable_domain_uses_private_suffixes() -> None:
    assert registrable_domain("www.apollohospitals.com") == "apollohospitals.com"
    assert registrable_domain("clinic.blogspot.com") == "clinic.blogspot.com"
    assert registrable_domain("x.co.uk") == "x.co.uk"
    assert registrable_domain("localhost") is None


def test_dedupe_key_drops_index_documents_and_unifies_slash() -> None:
    assert dedupe_key("https://a.com/about/index.html") == "https://a.com/about/"
    assert dedupe_key("https://a.com/about/", trailing_slash=False) == "https://a.com/about"
    assert dedupe_key("https://a.com/about", trailing_slash=True) == "https://a.com/about/"


@pytest.mark.parametrize(
    ("address", "public"),
    [
        ("8.8.8.8", True),
        ("10.0.0.1", False),
        ("127.0.0.1", False),
        ("169.254.169.254", False),
        ("100.64.1.1", False),
        ("::1", False),
        ("fd00::1", False),
        ("::ffff:10.0.0.1", False),
        ("2606:4700::1111", True),
    ],
)
def test_public_address_classes(address: str, public: bool) -> None:
    assert is_public_address(address) is public


async def test_ssrf_guard_checks_resolved_addresses_and_ports() -> None:
    async def resolver(host: str, port: int) -> list[str]:
        return {"rebind.example": ["10.1.2.3"], "ok.example": ["93.184.216.34"]}.get(host, [])

    guard = SsrfGuard(resolver)
    await guard.check("https://ok.example/")
    with pytest.raises(UnsafeTargetError):
        await guard.check("https://rebind.example/")
    with pytest.raises(UnsafeTargetError):
        await guard.check("https://ok.example:8080/")
    with pytest.raises(DnsLookupError):
        await guard.check("https://missing.example/")


async def test_site_resolver_follows_redirect_and_builds_scope() -> None:
    fetcher = FakeRawFetcher(
        {
            "https://apollohospitals.com/": Served(
                200,
                html_page("Apollo", "<p>" + "care " * 200 + "</p>"),
                final_url="https://www.apollohospitals.com/",
            )
        }
    )
    resolution = await SiteResolver(fetcher).resolve("apollohospitals.com", CTX)
    assert resolution.status == "ok"
    assert resolution.site is not None
    assert resolution.site.canonical_origin == "https://www.apollohospitals.com"
    assert set(resolution.site.scope_hosts) == {"www.apollohospitals.com", "apollohospitals.com"}


async def test_site_resolver_retries_waf_with_compat_agent() -> None:
    fetcher = FakeRawFetcher(
        {
            "https://example.com/": Served(
                200, html_page("Example", "<p>" + "text " * 100 + "</p>"), compat_only=True
            )
        }
    )
    resolution = await SiteResolver(fetcher).resolve("example.com", CTX)
    assert resolution.status == "ok"
    assert [mode.value for _, mode in fetcher.requests] == ["honest", "compat"]


@pytest.mark.parametrize(
    ("served", "status"),
    [
        (Served(200, b"<html><body>This domain is for sale! Buy this domain today.</body></html>"), "parked"),
        (Served(200, html_page("x", "<p>hi</p>"), final_url="https://www.instagram.com/clinic/"), "social"),
        (
            Served(200, html_page("x", "<p>" + "a " * 300 + "</p>"), final_url="https://newbrand.com/"),
            "moved",
        ),
        (Served(503, b"down"), "unreachable"),
        (Served(None), "unreachable"),
    ],
)
async def test_site_resolver_classifies_edge_cases(served: Served, status: str) -> None:
    resolution = await SiteResolver(FakeRawFetcher({"https://example.com/": served})).resolve(
        "example.com", CTX
    )
    assert resolution.status == status
    if status == "moved":
        assert resolution.moved_to == "newbrand.com"


async def test_site_resolver_rejects_ip_literals_and_social_input() -> None:
    resolver = SiteResolver(FakeRawFetcher({}))
    assert (await resolver.resolve("http://93.184.216.34/", CTX)).status == "invalid"
    assert (await resolver.resolve("instagram.com/myclinic", CTX)).status == "social"


def test_parked_heuristic_needs_offsite_links_on_thin_pages() -> None:
    thin = '<a href="https://ads.example/1">x</a><a href="https://ads.example/2">y</a><p>hello</p>'
    assert looks_parked(thin, "clinic.com")
    assert not looks_parked("<p>" + "real content " * 50 + "</p>", "clinic.com")
