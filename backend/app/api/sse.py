import asyncio
import contextlib
from collections import defaultdict
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from uuid import UUID

import anyio
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sse_starlette import ServerSentEvent

from app.api.schemas.runs import RunEventOut
from app.core.logging import get_logger
from app.domain.runs import RunStatus
from app.pipeline.lifecycle import RunStateMachine
from app.pipeline.progress import TERMINAL_RUN_EVENT_TYPES, EventLog, StoredEvent

logger = get_logger(__name__)

RETRY_MS = 3000
RECONNECT_MS = 1000
REPLAY_BATCH = 500


@dataclass(eq=False)
class Subscription:
    channel: str
    queue: asyncio.Queue[str]
    lagging: bool = False

    def drain(self) -> None:
        while not self.queue.empty():
            self.queue.get_nowait()
        self.lagging = False


@dataclass
class PubSubHub:
    """one redis pattern subscription per api process, fanned out to per-stream queues."""

    redis_url: str
    prefix: str
    queue_size: int = 256
    max_connections: int = 1000
    _subscriptions: dict[str, set[Subscription]] = field(default_factory=lambda: defaultdict(set))
    _task: asyncio.Task[None] | None = None
    _connected: bool = False
    _ready: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def healthy(self) -> bool:
        return self._connected

    @property
    def connections(self) -> int:
        return sum(len(subscriptions) for subscriptions in self._subscriptions.values())

    def at_capacity(self) -> bool:
        return self.connections >= self.max_connections

    async def start(self, wait_s: float = 2.0) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._listen())
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._ready.wait(), timeout=wait_s)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self._connected = False

    def register(self, channel: str) -> Subscription:
        subscription = Subscription(channel=channel, queue=asyncio.Queue(maxsize=self.queue_size))
        self._subscriptions[channel].add(subscription)
        return subscription

    def unregister(self, subscription: Subscription) -> None:
        subscriptions = self._subscriptions.get(subscription.channel)
        if subscriptions is not None:
            subscriptions.discard(subscription)
            if not subscriptions:
                self._subscriptions.pop(subscription.channel, None)

    def deliver(self, channel: str, data: str) -> None:
        for subscription in list(self._subscriptions.get(channel, ())):
            try:
                subscription.queue.put_nowait(data)
            except asyncio.QueueFull:
                # slow consumer: it backfills from the db instead of blocking everyone else
                subscription.lagging = True

    async def _listen(self) -> None:
        backoff = 0.5
        while True:
            client = Redis.from_url(self.redis_url, decode_responses=True)
            pubsub = client.pubsub()
            try:
                await pubsub.psubscribe(f"{self.prefix}:run:*", f"{self.prefix}:client:*")
                self._connected = True
                self._ready.set()
                backoff = 0.5
                async for message in pubsub.listen():
                    if message.get("type") == "pmessage":
                        self.deliver(message["channel"], message["data"])
            except (RedisError, OSError) as error:
                logger.warning("sse.hub_disconnected", error=type(error).__name__)
            finally:
                self._connected = False
                self._ready.set()
                with contextlib.suppress(RedisError, OSError):
                    await pubsub.aclose()
                    await client.aclose()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 10.0)


def frame(stored: StoredEvent, *, event_id: int | None = None) -> ServerSentEvent:
    event = stored.event
    return ServerSentEvent(
        data=RunEventOut.from_event(event).model_dump_json(exclude_none=True),
        event=event.type.value,
        id=str(event_id if event_id is not None else event.seq),
    )


async def run_event_stream(
    *,
    run_id: UUID,
    last_seq: int,
    log: EventLog,
    subscription: Subscription,
    hub: PubSubHub,
    run_status: Callable[[], Awaitable[RunStatus]],
    poll_s: float,
    shutdown: anyio.Event,
) -> AsyncIterator[ServerSentEvent]:
    """replay from the db, then live pub/sub with gap backfill; the db is the source of truth."""
    last = last_seq
    yield ServerSentEvent(retry=RETRY_MS)
    while True:
        batch = await log.after(run_id, last, limit=REPLAY_BATCH)
        for stored in batch:
            yield frame(stored)
            last = stored.event.seq
            if stored.event.type in TERMINAL_RUN_EVENT_TYPES:
                yield ServerSentEvent(event="end", data="")
                return
        if len(batch) < REPLAY_BATCH:
            break
    quiet_polls = 0
    while True:
        if shutdown.is_set():
            yield ServerSentEvent(event="reconnect", data="", retry=RECONNECT_MS)
            return
        try:
            message = await asyncio.wait_for(subscription.queue.get(), timeout=poll_s)
        except TimeoutError:
            message = None
        if message is None or subscription.lagging or not hub.healthy:
            if subscription.lagging:
                subscription.drain()
            pending = await log.after(run_id, last, limit=REPLAY_BATCH)
            if not pending:
                quiet_polls += 1
                # a terminal run whose last event never arrived still ends the stream
                if quiet_polls >= 2 and RunStateMachine.is_terminal(await run_status()):
                    yield ServerSentEvent(event="end", data="")
                    return
                continue
        else:
            stored = StoredEvent.from_message(message)
            if stored.event.seq <= last:
                continue
            pending = []
            if stored.event.seq > last + 1:
                pending = await log.between(run_id, last, stored.event.seq)
            pending.append(stored)
        quiet_polls = 0
        for stored in pending:
            if stored.event.seq <= last:
                continue
            yield frame(stored)
            last = stored.event.seq
            if stored.event.type in TERMINAL_RUN_EVENT_TYPES:
                yield ServerSentEvent(event="end", data="")
                return


async def client_event_stream(
    *,
    client_id: UUID,
    last_id: int,
    log: EventLog,
    subscription: Subscription,
    poll_s: float,
    shutdown: anyio.Event,
) -> AsyncIterator[ServerSentEvent]:
    """lifecycle events across a client's runs; ids are run_events row ids."""
    last = last_id
    yield ServerSentEvent(retry=RETRY_MS)
    while True:
        for stored in await log.client_after(client_id, last, limit=REPLAY_BATCH):
            yield frame(stored, event_id=stored.id)
            last = stored.id
        if shutdown.is_set():
            yield ServerSentEvent(event="reconnect", data="", retry=RECONNECT_MS)
            return
        # a live message only wakes the loop; the db replay keeps ids ordered across runs
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(subscription.queue.get(), timeout=poll_s)
        subscription.drain()
