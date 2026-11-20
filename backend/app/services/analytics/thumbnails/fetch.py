import hashlib
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx
import structlog

from app.core.registry import adapter_registry
from app.domain.youtube import VideoRecord

log = structlog.get_logger(__name__)

# only the api-returned thumbnail hosts are fetched, which keeps the fetcher from becoming an open proxy
ALLOWED_HOSTS = frozenset(
    {"i.ytimg.com", "i1.ytimg.com", "i2.ytimg.com", "i3.ytimg.com", "i4.ytimg.com", "i9.ytimg.com"}
)


class ImageSource(Protocol):
    async def fetch(self, url: str) -> bytes | None: ...


class YtimgImageSource:
    def __init__(self, http: httpx.AsyncClient, max_bytes: int = 2_000_000) -> None:
        self.http = http
        self.max_bytes = max_bytes

    async def fetch(self, url: str) -> bytes | None:
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS:
            raise ValueError(f"thumbnail url host is not allowed: {parts.hostname}")
        try:
            response = await self.http.get(url)
        except httpx.HTTPError as error:
            log.warning("thumbnail.fetch_failed", url=url, error=type(error).__name__)
            return None
        if response.status_code != 200:
            return None
        if len(response.content) > self.max_bytes:
            log.warning("thumbnail.too_large", url=url, size=len(response.content))
            return None
        return response.content


@adapter_registry.register("image_source", "ytimg_http")
def build_image_source(container: Any) -> YtimgImageSource:
    return YtimgImageSource(container.http)


@dataclass(frozen=True, slots=True)
class FetchedThumbnail:
    source_key: str
    url: str
    data: bytes

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()


class ThumbnailFetcher:
    def __init__(self, source: ImageSource, order: tuple[str, ...]) -> None:
        self.source = source
        self.order = order

    async def fetch_best(self, video: VideoRecord) -> FetchedThumbnail | None:
        # maxres and standard exist only for some videos, so smaller sizes are the fallback
        for key in self.order:
            ref = video.thumbnails.get(key)
            if ref is None:
                continue
            data = await self.source.fetch(ref.url)
            if data:
                return FetchedThumbnail(source_key=key, url=ref.url, data=data)
        return None
