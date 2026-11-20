import contextlib
import re
from collections import Counter
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel

from app.domain.web import FetchOutcome, FetchResult, SourceKind
from app.domain.youtube import ChannelRecord, SearchHit, SearchPage, SearchQuery, VideoRecord
from app.ports.llm import LLMError, LLMErrorKind, LLMResult, PromptInput, TaskId, TokenUsage
from app.ports.search import WebSearchHit, WebSearchQuery, WebSearchResponse
from app.ports.youtube import PlaylistItem, UploadsPage, VideoBatch
from app.services.competitors.resolve.resolution_cache import CachedResolution

NOW = datetime(2026, 11, 20, 12, tzinfo=UTC)


def channel(
    channel_id: str,
    title: str,
    *,
    handle: str | None = None,
    description: str = "",
    subscribers: int | None = 100_000,
    videos: int = 500,
    country: str | None = "IN",
    published_at: datetime = datetime(2012, 1, 1, tzinfo=UTC),
    made_for_kids: bool = False,
    availability: str = "available",
) -> ChannelRecord:
    return ChannelRecord(
        channel_id=channel_id,
        title=title,
        handle=handle,
        description=description,
        country=country,
        default_language="en",
        published_at=published_at,
        uploads_playlist_id=f"UU{channel_id[2:]}",
        subscriber_count=subscribers,
        hidden_subscriber_count=subscribers is None,
        view_count=1_000_000,
        video_count=videos,
        thumbnail_url=f"https://yt3.example/{channel_id}.jpg",
        made_for_kids=made_for_kids,
        availability=availability,
        metadata_fetched_at=NOW,
        stats_fetched_at=NOW,
    )


def video(
    video_id: str, channel_id: str, title: str, description: str = "", days_ago: int = 5
) -> VideoRecord:
    published = NOW - timedelta(days=days_ago)
    return VideoRecord(
        video_id=video_id,
        channel_id=channel_id,
        channel_title="channel",
        published_at=published,
        effective_published_at=published,
        title=title,
        description=description,
        content_hash="h",
        description_hash="h",
        duration_s=300,
        format="long_form",
        format_confidence="high",
        view_count=1000,
        ingest_source="uploads",
        metadata_fetched_at=NOW,
    )


class FakeYouTube:
    """an in-memory YouTubeGateway that records every call and its unit cost."""

    def __init__(self) -> None:
        self.channels: dict[str, ChannelRecord] = {}
        self.handles: dict[str, str] = {}
        self.usernames: dict[str, str] = {}
        self.videos: dict[str, VideoRecord] = {}
        self.uploads: dict[str, list[str]] = {}
        self.searches: dict[str, list[SearchHit]] = {}
        self.calls: Counter[str] = Counter()
        self.search_queries: list[SearchQuery] = []

    @property
    def units(self) -> int:
        return sum(count for name, count in self.calls.items() if name != "search")

    def add_channel(self, record: ChannelRecord, uploads: Sequence[VideoRecord] = ()) -> None:
        self.channels[record.channel_id] = record
        if record.handle:
            self.handles[record.handle.lstrip("@").casefold()] = record.channel_id
        for item in uploads:
            self.videos[item.video_id] = item
        self.uploads[record.uploads_playlist_id] = [item.video_id for item in uploads]

    async def channels_by_id(self, ids, ctx):
        self.calls["channels.list"] += 1
        return [self.channels[channel_id] for channel_id in ids if channel_id in self.channels]

    async def channel_by_handle(self, handle, ctx):
        self.calls["channels.list"] += 1
        channel_id = self.handles.get(handle.lstrip("@").casefold())
        return self.channels.get(channel_id) if channel_id else None

    async def channel_by_username(self, username, ctx):
        self.calls["channels.list"] += 1
        channel_id = self.usernames.get(username.casefold())
        return self.channels.get(channel_id) if channel_id else None

    async def uploads_page(self, playlist_id, page_token, ctx, etag=None):
        self.calls["playlistItems.list"] += 1
        ids = self.uploads.get(playlist_id, [])
        items = [
            PlaylistItem(video_id=video_id, published_at=self.videos[video_id].published_at)
            for video_id in ids
        ]
        return UploadsPage(
            playlist_id=playlist_id, items=items, next_page_token=None, etag=None, fetched_at=NOW
        )

    async def videos_by_id(self, ids, ctx, with_localizations=False):
        self.calls["videos.list"] += 1
        if len(ids) > 50:
            raise AssertionError("videos.list takes at most 50 ids")
        found = [self.videos[video_id] for video_id in ids if video_id in self.videos]
        return VideoBatch(
            found=found, missing_ids=[video_id for video_id in ids if video_id not in self.videos]
        )

    async def search(self, query, page_token, ctx):
        self.calls["search"] += 1
        self.search_queries.append(query)
        hits = self.searches.get(query.q, [])
        return SearchPage(
            query_key=query.q,
            items=hits,
            fetched_at=NOW,
            expires_at=NOW + timedelta(days=1),
            from_cache=False,
        )

    async def playlist_owner(self, playlist_id, ctx):
        self.calls["playlists.list"] += 1
        return None

    async def video_stats(self, ids, ctx):
        raise AssertionError("discovery never needs stats")

    async def comment_threads(self, video_id, max_results, ctx):
        raise AssertionError("discovery never reads comments")


class FakeWebSearch:
    def __init__(self, routes: dict[str, list[WebSearchHit]] | None = None, *, fail: bool = False) -> None:
        self.routes = routes or {}
        self.fail = fail
        self.queries: list[WebSearchQuery] = []

    @property
    def credits(self) -> int:
        return len(self.queries)

    async def search(self, query: WebSearchQuery) -> WebSearchResponse:
        self.queries.append(query)
        if self.fail:
            raise ConnectionError("tavily unavailable")
        hits = next((hits for pattern, hits in self.routes.items() if pattern in query.query), [])
        if query.include_domains:
            hits = [hit for hit in hits if any(domain in hit.url for domain in query.include_domains)]
        return WebSearchResponse(query=query, hits=hits, credits_used=1.0, from_cache=False, fetched_at=NOW)

    async def extract(self, request):
        raise AssertionError("extract is not a resolution source")

    async def map(self, request):
        raise AssertionError("not used")

    async def crawl(self, request):
        raise AssertionError("not used")


def hit(url: str, title: str, snippet: str = "") -> WebSearchHit:
    return WebSearchHit(url=url, title=title, snippet=snippet)


def page(
    url: str, html: str = "", *, outcome: FetchOutcome = FetchOutcome.ok, status: int = 200, json_ld=None
) -> FetchResult:
    return FetchResult(
        requested_url=url,
        final_url=url,
        source_kind=SourceKind.site_direct,
        outcome=outcome,
        status_code=status,
        html=html or None,
        json_ld=json_ld or [],
        fetched_at=NOW,
    )


class FakeCrawler:
    def __init__(self, pages: dict[str, FetchResult]) -> None:
        self.pages = pages
        self.fetched: list[str] = []

    async def fetch_page(self, url, ctx):
        self.fetched.append(url)
        if url in self.pages:
            return self.pages[url]
        return page(url, outcome=FetchOutcome.not_found, status=404)

    async def crawl(self, site, budget, reporter, previous=None):
        raise AssertionError("discovery never crawls whole sites")


Handler = Callable[[PromptInput], dict[str, Any]]


class FakeLLM:
    """returns validated fixtures per task; each call costs a fixed amount so budgets are testable."""

    def __init__(
        self, handlers: dict[TaskId, Handler] | None = None, *, down: bool = False, cost: float = 0.01
    ) -> None:
        self.handlers = handlers or {}
        self.down = down
        self.cost = cost
        self.calls: Counter[TaskId] = Counter()

    async def generate_structured(
        self, *, task, schema: type[BaseModel], system, input, context, tier=None, budget=None
    ):
        self.calls[task] += 1
        if self.down:
            raise LLMError(LLMErrorKind.circuit_open, "provider down")
        handler = self.handlers.get(task)
        if handler is None:
            raise LLMError(LLMErrorKind.invalid_output, f"no fixture for {task}")
        value = schema.model_validate(handler(input))
        return LLMResult[schema](
            value=value,
            raw_text="{}",
            task=task,
            prompt_id=system.prompt_id,
            prompt_version="v1",
            provider="fake",
            model="fake",
            route_index=0,
            attempts=1,
            repaired=False,
            usage=TokenUsage(input_tokens=100, output_tokens=50),
            cost_usd=self.cost,
            latency_ms=1,
            stop_reason="end_turn",
        )

    async def generate_text(self, **kwargs):
        raise AssertionError("discovery only uses structured outputs")


def untrusted_entities(prompt: PromptInput) -> list[tuple[str, str]]:
    """(key, name) for each entity block of a relation classification prompt."""
    entities = []
    for block in prompt.untrusted:
        key = block.label.removeprefix("entity ")
        name = re.search(r"^name: (.*)$", block.text, re.MULTILINE)
        entities.append((key, name.group(1) if name else ""))
    return entities


class InMemoryBrandCache:
    def __init__(self) -> None:
        self.entries: list[CachedResolution] = []

    async def lookup(self, keys, now):
        wanted = set(keys)
        return [entry for entry in self.entries if (entry.key_type, entry.key) in wanted]

    async def store(self, entries, ttl_days):
        self.entries.extend(entries)


class RecordingReporter:
    def __init__(self) -> None:
        self.steps: list[str] = []
        self.insights: list[Any] = []

    async def step(self, step, message, payload=None):
        self.steps.append(message)

    async def progress(self, done, total, unit):
        return None

    async def insight(self, message, payload):
        self.insights.append(payload)

    async def notice(self, notice):
        return None


class MemoryLocks:
    def __init__(self) -> None:
        self.held: set[str] = set()

    @contextlib.asynccontextmanager
    async def acquire(self, key, ttl_s, wait_s=None):
        if key in self.held:
            raise TimeoutError(key)
        self.held.add(key)
        try:
            yield key
        finally:
            self.held.discard(key)

    async def single_flight(self, key, ttl_s, fn):
        return await fn()
