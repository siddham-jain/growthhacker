import asyncio
import uuid
from datetime import datetime

import anyio
import pytest
from sqlalchemy import insert

from app.api import ids
from app.api.schemas.results import TrendingData
from app.api.sse import PubSubHub, run_event_stream
from app.core.tenancy import TenantScope
from app.domain.analytics import TrendingReport
from app.domain.runs import RunKind, RunStatus, RunTrigger
from app.models.runs import ActiveResultSet
from app.pipeline.progress import EventLog
from app.pipeline.runs import RunRequest
from tests.api.conftest import Account, Harness
from tests.pipeline.support import run_pipeline

pytestmark = pytest.mark.db


async def _finished_run(harness: Harness, account: Account) -> tuple[TenantScope, uuid.UUID, int]:
    client = await harness.create_client(account)
    scope = TenantScope(
        workspace_id=ids.decode(ids.WORKSPACE, account.workspace_id),
        client_id=ids.decode(ids.CLIENT, client["id"]),
    )
    run_id, status = await run_pipeline(harness.container, harness.state.pipeline, scope)
    assert status is RunStatus.succeeded
    events = await EventLog(harness.container.session_factory, scope).after(run_id, 0)
    return scope, run_id, len(events)


def _frames(body: str) -> list[dict[str, str]]:
    frames = []
    for block in body.replace("\r\n", "\n").split("\n\n"):
        fields: dict[str, str] = {}
        for line in block.split("\n"):
            name, _, value = line.partition(": ")
            if name in {"id", "event", "data", "retry"}:
                fields[name] = value
        if fields:
            frames.append(fields)
    return frames


async def test_sse_replays_after_last_event_id_and_ends(harness: Harness) -> None:
    account = await harness.account("sse")
    _, run_id, total = await _finished_run(harness, account)
    path = f"/api/v1/runs/{ids.encode(ids.RUN, run_id)}/events"

    response = await harness.client.get(path, headers={**account.headers, "Last-Event-ID": "2"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-accel-buffering"] == "no" and response.headers["cache-control"] == "no-cache"
    assert "content-encoding" not in response.headers
    frames = _frames(response.text)
    assert frames[0] == {"retry": "3000"}
    numbered = [frame for frame in frames if "id" in frame]
    assert [int(frame["id"]) for frame in numbered] == list(range(3, total + 1))
    assert numbered[-1]["event"] == "run.succeeded"
    assert frames[-1]["event"] == "end"

    finished = await harness.client.get(path, headers={**account.headers, "Last-Event-ID": str(total)})
    assert finished.status_code == 204


async def test_stream_token_and_polling_fallback(harness: Harness) -> None:
    account = await harness.account("token")
    _, run_id, total = await _finished_run(harness, account)
    run = ids.encode(ids.RUN, run_id)
    token = (await harness.client.post(f"/api/v1/runs/{run}/stream-token", headers=account.headers)).json()[
        "token"
    ]

    streamed = await harness.client.get(
        f"/api/v1/runs/{run}/events", params={"token": token, "after_seq": total - 1}
    )
    assert streamed.status_code == 200
    assert [frame["event"] for frame in _frames(streamed.text) if "id" in frame] == ["run.succeeded"]
    other_run = await harness.client.get(
        f"/api/v1/runs/{ids.encode(ids.RUN, uuid.uuid4())}/events", params={"token": token}
    )
    assert other_run.status_code == 401

    polled = await harness.client.get(
        f"/api/v1/runs/{run}/events.json", params={"after_seq": 1}, headers=account.headers
    )
    body = polled.json()
    assert body["run_status"] == "succeeded" and body["last_seq"] == total
    assert [item["seq"] for item in body["items"]] == list(range(2, total + 1))
    assert body["items"][0]["run_id"] == run


async def test_live_stream_backfills_a_missed_pubsub_message(harness: Harness) -> None:
    account = await harness.account("gap")
    client = await harness.create_client(account)
    scope = TenantScope(
        workspace_id=ids.decode(ids.WORKSPACE, account.workspace_id),
        client_id=ids.decode(ids.CLIENT, client["id"]),
    )
    run = (
        await harness.state.pipeline.runs.request_run(
            scope, RunRequest(kind=RunKind.analysis, trigger=RunTrigger.user)
        )
    ).run
    log = EventLog(harness.container.session_factory, scope)

    def row(event_type: str, message: str) -> dict:
        return {
            "type": event_type,
            "stage": None,
            "step": None,
            "status": None,
            "level": "info",
            "message": message,
            "counts": None,
            "percent": None,
            "stage_percent": None,
            "payload": None,
        }

    hub = PubSubHub(redis_url="redis://unused", prefix="unused")
    hub._connected = True
    subscription = hub.register("run")
    collected: list[str] = []

    async def consume() -> None:
        async for frame in run_event_stream(
            run_id=run.id,
            last_seq=1,
            log=log,
            subscription=subscription,
            hub=hub,
            run_status=lambda: _status(RunStatus.running),
            poll_s=5,
            shutdown=anyio.Event(),
        ):
            collected.append(frame.event or "retry")

    consumer = asyncio.create_task(consume())
    await asyncio.sleep(0.1)
    stored = await log.append(run.id, [row("stage.step", "missed"), row("stage.step", "seen")])
    # only the second message reaches the subscriber; the stream must fetch the first from the db
    subscription.queue.put_nowait(stored[-1].to_message())
    terminal = await log.append(run.id, [row("run.succeeded", "done")])
    subscription.queue.put_nowait(terminal[0].to_message())
    await asyncio.wait_for(consumer, timeout=5)
    assert collected == ["retry", "stage.step", "stage.step", "run.succeeded", "end"]


async def _status(status: RunStatus) -> RunStatus:
    return status


class FakeAnalytics:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def trending(self, scope: TenantScope, run_id: uuid.UUID, window_days: int) -> TrendingData:
        self.calls.append((scope.client_id, run_id, window_days))
        report = TrendingReport(
            client_id=str(scope.client_id),
            window_days=window_days,
            as_of=datetime.fromisoformat("2026-11-20T00:00:00+00:00"),
            items=[],
            rising_topics=[],
        )
        return TrendingData(report=report)


async def test_results_are_wrapped_in_the_published_envelope(harness: Harness) -> None:
    account = await harness.account("envelope")
    scope, run_id, _ = await _finished_run(harness, account)
    analytics = FakeAnalytics()
    harness.state.override_service("analytics", analytics)
    client_path = f"/api/v1/clients/{ids.encode(ids.CLIENT, scope.client_id)}"

    missing = await harness.client.get(f"{client_path}/analytics/trending", headers=account.headers)
    assert missing.status_code == 404 and missing.json()["code"] == "results_not_ready"

    async with harness.container.session_factory() as session, session.begin():
        await session.execute(
            insert(ActiveResultSet).values(
                workspace_id=scope.workspace_id,
                client_id=scope.client_id,
                window_days=30,
                run_id=run_id,
                published_at=harness.container.clock.now(),
                stale_reasons=["config_changed"],
            )
        )
    response = await harness.client.get(f"{client_path}/analytics/trending", headers=account.headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["run_id"] == ids.encode(ids.RUN, run_id) and body["window_days"] == 30
    assert body["freshness"]["stale"] is True and body["freshness"]["stale_reasons"] == ["config_changed"]
    assert body["data"]["report"]["window_days"] == 30
    assert analytics.calls == [(scope.client_id, run_id, 30)]
