from urllib.parse import quote

import httpx

from app.services.website_intel.acquisition.ports import ReaderDocument
from app.services.website_intel.crawl.blocks import BlockClassifier
from app.services.website_intel.models import PageVerdict


class JinaReader:
    """r.jina.ai returns a markdown copy fetched from its own egress; a 200 can still wrap a waf page."""

    name = "jina_reader"

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        base_url: str = "https://r.jina.ai/",
        user_agent: str,
        timeout_s: float = 30,
        classifier: BlockClassifier | None = None,
    ) -> None:
        self._client = client
        self._base_url = base_url
        self._user_agent = user_agent
        self._timeout_s = timeout_s
        self._classifier = classifier or BlockClassifier()

    async def read(self, url: str) -> ReaderDocument:
        try:
            response = await self._client.get(
                self._base_url + quote(url, safe=":/?&=%#@+,;~"),
                headers={"Accept": "application/json", "User-Agent": self._user_agent},
                timeout=self._timeout_s,
                follow_redirects=True,
            )
        except httpx.HTTPError:
            return ReaderDocument(url, url, None, "", PageVerdict.error, None)
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        data = payload.get("data") if isinstance(payload, dict) else None
        if response.status_code != 200 or not isinstance(data, dict):
            verdict = self._classifier.classify(
                status_code=response.status_code, headers=dict(response.headers), body=response.text[:20_000]
            )
            return ReaderDocument(url, url, None, "", verdict, response.status_code)
        title = data.get("title") or None
        content = data.get("content") or ""
        upstream_status = data.get("status") if isinstance(data.get("status"), int) else 200
        verdict = self._classifier.classify(
            status_code=upstream_status,
            headers={},
            body=f"{title or ''}\n{content}",
            content_type="text/markdown",
        )
        if verdict is PageVerdict.ok and "target url returned error" in content.lower():
            verdict = PageVerdict.waf_block
        final_url = data.get("url") or url
        return ReaderDocument(url, final_url, title, content, verdict, upstream_status)
