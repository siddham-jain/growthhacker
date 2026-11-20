from datetime import UTC, datetime

import httpx
import respx

from app.adapters.freshness.base import FreshnessDeps
from app.adapters.freshness.rss import RssSource
from tests.news.adapters.conftest import SINCE, planned

FEED_URL = "https://openai.com/news/rss.xml"
RSS = b"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>News</title>
<item><title>New model</title><link>https://openai.com/index/new-model</link>
<pubDate>Tue, 17 Nov 2026 16:00:00 GMT</pubDate>
<description>&lt;p&gt;We are &lt;b&gt;launching&lt;/b&gt; a model.&lt;/p&gt;</description></item>
<item><title>Undated note</title><link>https://openai.com/index/undated</link></item>
<item><title>Old post</title><link>https://openai.com/index/old</link>
<pubDate>Mon, 02 Nov 2026 10:00:00 GMT</pubDate></item>
</channel></rss>"""
ATOM = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Blog</title>
<entry><title>Atom entry</title><link href="https://deepmind.google/blog/atom-entry"/>
<id>urn:1</id><updated>2026-11-18T09:30:00Z</updated><summary>Short summary</summary></entry>
</feed>"""


@respx.mock
async def test_rss_items_dates_and_html(deps: FreshnessDeps) -> None:
    respx.get(FEED_URL).mock(return_value=httpx.Response(200, content=RSS))
    result = await RssSource(deps).fetch(planned("rss", "openai_news", url=FEED_URL), SINCE)
    assert result.status == "ok"
    assert [item.title for item in result.items] == ["New model", "Undated note"]
    dated, undated = result.items
    assert dated.published_at == datetime(2026, 11, 17, 16, 0, tzinfo=UTC)
    assert dated.snippet == "We are launching a model."
    assert undated.published_at is None


@respx.mock
async def test_atom_feed(deps: FreshnessDeps) -> None:
    url = "https://deepmind.google/blog/rss.xml"
    respx.get(url).mock(return_value=httpx.Response(200, content=ATOM))
    result = await RssSource(deps).fetch(planned("rss", "deepmind", url=url), SINCE)
    assert result.items[0].url == "https://deepmind.google/blog/atom-entry"
    assert result.items[0].published_at == datetime(2026, 11, 18, 9, 30, tzinfo=UTC)


@respx.mock
async def test_missing_feed_is_an_error(deps: FreshnessDeps) -> None:
    url = "https://www.anthropic.com/rss.xml"
    respx.get(url).mock(return_value=httpx.Response(404))
    result = await RssSource(deps).fetch(planned("rss", "anthropic", url=url), SINCE)
    assert result.status == "error"
    assert "404" in (result.error or "")


async def test_query_without_url_is_an_error(deps: FreshnessDeps) -> None:
    result = await RssSource(deps).fetch(planned("rss", "nothing"), SINCE)
    assert result.status == "error"
