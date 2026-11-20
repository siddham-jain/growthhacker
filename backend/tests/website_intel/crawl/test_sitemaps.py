import gzip
from datetime import UTC, datetime

from app.services.website_intel.crawl.sitemaps import (
    SitemapEntry,
    SitemapReader,
    order_child_sitemaps,
    parse_lastmod,
)

APOLLO_HOSTS = {"www.apollohospitals.com"}


def _urlset(urls: list[str]) -> bytes:
    body = "".join(f"<url><loc>{url}</loc></url>" for url in urls)
    return f'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>'.encode()


def test_apollo_index_lists_children(fixture_path) -> None:
    content = fixture_path("crawl_apollo_sitemap_index.xml").read_bytes()
    parsed = SitemapReader().parse(
        content, url="https://www.apollohospitals.com/sitemap.xml", allowed_hosts=APOLLO_HOSTS
    )
    assert len(parsed.children) == 6
    assert not parsed.entries
    assert parsed.children[0].lastmod == datetime.fromisoformat("2026-11-15T08:32:33+05:30")


def test_apollo_urlset_keeps_lastmod_priority_changefreq(fixture_path) -> None:
    content = fixture_path("crawl_apollo_urlset_page1.xml").read_bytes()
    parsed = SitemapReader().parse(
        content, url="https://www.apollohospitals.com/sitemap.xml?page=1", allowed_hosts=APOLLO_HOSTS
    )
    assert len(parsed.entries) == 25
    home = parsed.entries[0]
    assert home.url == "https://www.apollohospitals.com/"
    assert home.priority == 1.0 and home.changefreq == "daily"
    assert home.lastmod is not None and home.lastmod.tzinfo is not None


def test_gzip_and_same_host_rule() -> None:
    content = gzip.compress(_urlset(["https://a.com/x", "https://b.com/y", "https://a.com/z"]))
    parsed = SitemapReader().parse(content, url="https://a.com/sitemap.xml.gz", allowed_hosts={"a.com"})
    assert [entry.url for entry in parsed.entries] == ["https://a.com/x", "https://a.com/z"]
    assert parsed.dropped_off_host == 1


def test_url_cap_truncates() -> None:
    content = _urlset([f"https://a.com/p{index}" for index in range(30)])
    parsed = SitemapReader(max_urls_per_file=10).parse(
        content, url="https://a.com/s.xml", allowed_hosts={"a.com"}
    )
    assert len(parsed.entries) == 10 and parsed.truncated


def test_decompression_bomb_is_aborted() -> None:
    content = gzip.compress(b"<urlset>" + b" " * 5_000_000 + b"</urlset>")
    parsed = SitemapReader(max_bytes=1_000_000).parse(
        content, url="https://a.com/s.xml.gz", allowed_hosts={"a.com"}
    )
    assert parsed.truncated and not parsed.entries


def test_entities_are_never_expanded() -> None:
    bomb = (
        b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;">'
        b'<!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;">]>'
        b"<urlset><url><loc>https://a.com/&lol3;</loc></url><url><loc>https://a.com/ok</loc></url></urlset>"
    )
    parsed = SitemapReader().parse(bomb, url="https://a.com/s.xml", allowed_hosts={"a.com"})
    assert all("lollol" not in entry.url for entry in parsed.entries)
    assert "https://a.com/ok" in [entry.url for entry in parsed.entries]


def test_plain_text_rss_and_atom() -> None:
    reader = SitemapReader()
    text = reader.parse(
        b"https://a.com/one\n\nnot a url\nhttps://a.com/two\n",
        url="https://a.com/sitemap.txt",
        allowed_hosts={"a.com"},
    )
    assert [entry.url for entry in text.entries] == ["https://a.com/one", "https://a.com/two"]
    rss = reader.parse(
        b"<rss><channel><item><link>https://a.com/post</link><pubDate>Tue, 10 Nov 2026 10:00:00 GMT</pubDate>"
        b"</item></channel></rss>",
        url="https://a.com/feed",
        allowed_hosts={"a.com"},
    )
    assert rss.entries[0].url == "https://a.com/post"
    assert rss.entries[0].lastmod == datetime(2026, 11, 10, 10, tzinfo=UTC)
    atom = reader.parse(
        b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><link href="https://a.com/e1"/>'
        b"<updated>2026-11-01T00:00:00Z</updated></entry></feed>",
        url="https://a.com/atom",
        allowed_hosts={"a.com"},
    )
    assert atom.entries[0].url == "https://a.com/e1"


def test_parse_lastmod_variants() -> None:
    assert parse_lastmod("2026-11-01") == datetime(2026, 11, 1, tzinfo=UTC)
    assert parse_lastmod("garbage") is None
    assert parse_lastmod(None) is None


def test_child_ordering_samples_negative_groups(fixture_path) -> None:
    content = fixture_path("crawl_apollo_doctors_sitemap_index.xml").read_bytes()
    doctors = (
        SitemapReader()
        .parse(content, url="https://www.apollohospitals.com/x.xml", allowed_hosts=APOLLO_HOSTS)
        .children
    )
    assert len(doctors) > 20
    children = [
        SitemapEntry("https://a.com/sitemap-misc.xml"),
        SitemapEntry("https://a.com/sitemap-services.xml", lastmod=datetime(2026, 1, 1, tzinfo=UTC)),
        SitemapEntry("https://a.com/sitemap-pages.xml", lastmod=datetime(2026, 6, 1, tzinfo=UTC)),
        *doctors,
    ]
    ordered = order_child_sitemaps(children, max_files=20)
    names = [entry.url for entry in ordered]
    assert names[:3] == [
        "https://a.com/sitemap-pages.xml",
        "https://a.com/sitemap-services.xml",
        "https://a.com/sitemap-misc.xml",
    ]
    assert sum("doctor" in name for name in names) == 2


def test_child_ordering_keeps_all_under_cap() -> None:
    children = [SitemapEntry(f"https://a.com/blog-{index}.xml") for index in range(5)]
    assert len(order_child_sitemaps(children, max_files=20)) == 5
