import gzip
import json
from datetime import UTC, datetime

import httpx

from app.services.website_intel.acquisition.ports import ArchivedPage
from app.services.website_intel.crawl.fetching import decode_body

WARC_HEADER_END = b"\r\n\r\n"


def _timestamp(value: str) -> datetime:
    return datetime.strptime(value[:14].ljust(14, "0"), "%Y%m%d%H%M%S").replace(tzinfo=UTC)


class WaybackArchive:
    """latest 200 html capture from the wayback cdx api, fetched raw through the id_ url form."""

    name = "wayback"

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        cdx_url: str,
        raw_url_template: str,
        user_agent: str,
        timeout_s: float = 30,
    ) -> None:
        self._client = client
        self._cdx_url = cdx_url
        self._raw_url = raw_url_template
        self._headers = {"User-Agent": user_agent}
        self._timeout_s = timeout_s

    async def latest(self, url: str) -> ArchivedPage | None:
        params = {
            "url": url,
            "output": "json",
            "filter": ["statuscode:200", "mimetype:text/html"],
            "fl": "timestamp,original,mimetype",
            "limit": "-1",
        }
        try:
            response = await self._client.get(
                self._cdx_url, params=params, headers=self._headers, timeout=self._timeout_s
            )
            rows = response.json() if response.status_code == 200 and response.content.strip() else []
        except (httpx.HTTPError, ValueError):
            return None
        captures = [row for row in rows[1:] if len(row) >= 2]
        if not captures:
            return None
        timestamp, original = captures[-1][0], captures[-1][1]
        raw_url = self._raw_url.format(timestamp=timestamp, url=original)
        try:
            page = await self._client.get(
                raw_url, headers=self._headers, timeout=self._timeout_s, follow_redirects=True
            )
        except httpx.HTTPError:
            return None
        if page.status_code != 200:
            return None
        content_type = page.headers.get("content-type")
        return ArchivedPage(
            original_url=original,
            archive_url=raw_url,
            snapshot_at=_timestamp(timestamp),
            status_code=page.status_code,
            content_type=content_type,
            html=decode_body(page.content, content_type),
        )


def parse_warc_response(record: bytes) -> tuple[int, str | None, bytes] | None:
    """splits a gzip warc response record into http status, content type and body."""
    try:
        data = gzip.decompress(record)
    except (OSError, EOFError):
        return None
    warc_end = data.find(WARC_HEADER_END)
    if warc_end < 0:
        return None
    http = data[warc_end + len(WARC_HEADER_END) :]
    http_end = http.find(WARC_HEADER_END)
    if http_end < 0:
        return None
    head = http[:http_end].decode("iso-8859-1").split("\r\n")
    try:
        status = int(head[0].split()[1])
    except (IndexError, ValueError):
        return None
    content_type = next(
        (line.split(":", 1)[1].strip() for line in head[1:] if line.lower().startswith("content-type:")), None
    )
    return status, content_type, http[http_end + len(WARC_HEADER_END) :]


class CommonCrawlArchive:
    """common crawl url index lookup plus a single ranged warc read; the wayback fallback."""

    name = "common_crawl"

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        collections_url: str,
        data_url: str,
        user_agent: str,
        timeout_s: float = 30,
    ) -> None:
        self._client = client
        self._collections_url = collections_url
        self._data_url = data_url
        self._headers = {"User-Agent": user_agent}
        self._timeout_s = timeout_s
        self._index_api: str | None = None

    async def _latest_index(self) -> str | None:
        if self._index_api is None:
            try:
                response = await self._client.get(
                    self._collections_url, headers=self._headers, timeout=self._timeout_s
                )
                collections = response.json() if response.status_code == 200 else []
            except (httpx.HTTPError, ValueError):
                return None
            self._index_api = collections[0]["cdx-api"] if collections else None
        return self._index_api

    async def latest(self, url: str) -> ArchivedPage | None:
        index_api = await self._latest_index()
        if index_api is None:
            return None
        try:
            response = await self._client.get(
                index_api,
                params={"url": url, "output": "json", "filter": "status:200"},
                headers=self._headers,
                timeout=self._timeout_s,
            )
        except httpx.HTTPError:
            return None
        if response.status_code != 200:
            return None
        lines = [json.loads(line) for line in response.text.splitlines() if line.strip().startswith("{")]
        htmls = [line for line in lines if "html" in line.get("mime", "")]
        if not htmls:
            return None
        entry = htmls[-1]
        offset, length = int(entry["offset"]), int(entry["length"])
        try:
            record = await self._client.get(
                self._data_url + entry["filename"],
                headers={**self._headers, "Range": f"bytes={offset}-{offset + length - 1}"},
                timeout=self._timeout_s,
            )
        except httpx.HTTPError:
            return None
        parsed = parse_warc_response(record.content) if record.status_code in {200, 206} else None
        if parsed is None or parsed[0] != 200:
            return None
        status, content_type, body = parsed
        return ArchivedPage(
            original_url=entry.get("url", url),
            archive_url=self._data_url + entry["filename"],
            snapshot_at=_timestamp(entry["timestamp"]),
            status_code=status,
            content_type=content_type,
            html=decode_body(body, content_type),
        )
