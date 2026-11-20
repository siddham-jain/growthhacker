from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from uuid import UUID

import cv2
import numpy as np

from app.core.clock import FrozenClock
from app.core.registry import stage_registry
from app.domain.analytics import AgeMethod
from app.domain.common import ChannelRole
from app.domain.runs import OutlierFoundPayload, RunKind, RunTrigger
from app.domain.youtube import ThumbnailRef
from app.models.youtube_derived import YtVideoTrait
from app.pipeline.contracts import BudgetKind, Market, RunConfigSnapshot, RunContext, UsageLedger
from app.pipeline.stages.analytics import EnrichStage
from app.services.analytics.engine import AnalyticsEngine
from app.services.analytics.settings import AnalyticsEngineSettings
from app.services.analytics.storage import AnalyticsStore
from app.services.analytics.thumbnails.text import TextBox
from tests.analytics.apollo_fixture import APOLLO, COMPETITORS
from tests.analytics.factories import AS_OF, CLIENT_ID, make_channel, make_inputs, make_video, scoped
from tests.analytics.test_golden import build_inputs
from tests.conftest import make_settings

RUN_ID = UUID("00000000-0000-0000-0000-0000000000f6")


class Reporter:
    def __init__(self) -> None:
        self.steps: list[str] = []
        self.insights: list[Any] = []

    async def step(self, step, message, payload=None):
        self.steps.append(step)

    async def progress(self, done, total, unit):
        return None

    async def insight(self, message, payload):
        self.insights.append(payload)

    async def notice(self, notice):
        return None


class Ledger:
    def ensure(self, kind: BudgetKind, amount: float) -> None: ...

    def charge(self, kind: BudgetKind, amount: float) -> None: ...

    def remaining(self, kind: BudgetKind) -> float | None:
        return None

    def usage(self) -> UsageLedger:
        return UsageLedger()


class NoCancel:
    def is_cancelled(self) -> bool:
        return False

    async def checkpoint(self) -> None:
        return None


class Artifacts:
    async def get(self, key):
        raise KeyError(key.name)

    async def get_optional(self, key):
        return None


class StaticSource:
    def __init__(self, inputs) -> None:
        self.inputs = inputs
        self.requests = []

    async def load(self, request):
        self.requests.append(request)
        return self.inputs


class MemoryStore(AnalyticsStore):
    def __init__(self) -> None:
        self.written: dict[str, int] = {}
        self.thumbnails = []
        self.recurring: dict[str, Any] = {}

    async def write(self, result, inputs):
        rows = self.rows(result, inputs)
        self.written = {table.name: len(items) for table, _, items in rows}
        return self.written

    async def write_thumbnails(self, traits, urls, verified_at):
        self.thumbnails += list(traits)
        return len(traits)

    async def write_recurring_text(self, channel_id, shares, as_of):
        self.recurring[channel_id] = shares
        return len(shares)

    async def recurring_text(self, channel_id, since):
        return None


@dataclass
class Deps:
    analytics_inputs: Any
    analytics_store: Any
    image_source: Any = None
    thumbnail_text_reader: Any = None
    settings: Any = field(default_factory=make_settings)


def context(deps: Deps, window: int = 30) -> RunContext[Deps]:
    snapshot = RunConfigSnapshot(
        workspace_id=CLIENT_ID,
        client_id=CLIENT_ID,
        config_version=1,
        config_hash="h",
        window_days=window,
        own_channel_id=APOLLO,
        competitor_channel_ids=list(COMPETITORS),
        topic_set_version=1,
        market=Market(country="IN", language="en", timezone="Asia/Kolkata"),
    )
    return RunContext(
        run_id=RUN_ID,
        kind=RunKind.analysis,
        trigger=RunTrigger.user,
        attempt=1,
        as_of=AS_OF,
        snapshot=snapshot,
        clock=FrozenClock(AS_OF),
        budget=Ledger(),
        reporter=Reporter(),
        artifacts=Artifacts(),
        cancel=NoCancel(),
        deps=deps,
    )


async def test_analytics_stage_produces_the_result_and_persists_shared_rows():
    store = MemoryStore()
    source = StaticSource(build_inputs())
    ctx = context(Deps(analytics_inputs=source, analytics_store=store))
    stage = stage_registry.get("analytics")()
    result = await stage.run(ctx)
    assert result.status == "succeeded"
    assert set(result.artifact.windows) == {7, 30, 90}
    assert source.requests[0].own_channel_id == APOLLO and source.requests[0].timezone == "Asia/Kolkata"
    assert ctx.reporter.steps == ["analytics.baselines_done", "analytics.patterns_done"]
    assert all(isinstance(p, OutlierFoundPayload) for p in ctx.reporter.insights[:1])
    assert store.written["yt_video_metrics"] > 0 and store.written["yt_channel_baselines"] > 0
    assert result.counts["outliers"] >= 1
    assert stage.fingerprint_inputs(ctx)["windows"] == [7, 30, 90]


def test_shared_rows_never_hold_client_or_owner_data():
    inputs = build_inputs()
    result = AnalyticsEngine(AnalyticsEngineSettings()).run(inputs, AS_OF)
    rows = {table.name: items for table, _, items in MemoryStore().rows(result, inputs)}
    trait = rows[YtVideoTrait.__tablename__][0]["traits"]
    assert "has_client_location" not in trait["title"]
    assert "has_client_site_link" not in trait["description"]
    assert all("matched_buyer_questions" not in r and "excerpts" not in r for r in rows["yt_comment_signals"])
    assert all(r["method"] != AgeMethod.owner_analytics.value for r in rows["yt_video_age_values"])


def png(image: np.ndarray) -> bytes:
    ok, data = cv2.imencode(".png", image)
    assert ok
    return data.tobytes()


class Images:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def fetch(self, url: str) -> bytes | None:
        self.calls.append(url)
        if "missing" in url:
            return None
        image = np.full((360, 480, 3), 230, dtype=np.uint8)
        image[100:200, 50:400] = (20, 20, 160)
        return png(image)


HEADLINES = [
    "KNEE PAIN FIXED",
    "HEART CARE TIPS",
    "SLEEP BETTER TONIGHT",
    "BACK PAIN RELIEF",
    "EAT FOR SUGAR",
    "WALK MORE DAILY",
    "STOP JOINT ACHE",
    "EYE STRAIN HELP",
]


class Reader:
    engine, model = "fake", "fake-1"

    def __init__(self) -> None:
        self.count = 0

    def read(self, image, script):
        height, width = image.shape[:2]
        self.count += 1
        headline = HEADLINES[self.count % len(HEADLINES)]
        return [
            TextBox(((40, 100), (400, 100), (400, 200), (40, 200)), headline, 0.93),
            TextBox(
                (
                    (width - 200, height - 90),
                    (width - 20, height - 90),
                    (width - 20, height - 20),
                    (width - 200, height - 20),
                ),
                "Clinic",
                0.9,
            ),
        ]


async def test_enrich_stage_builds_thumbnail_traits_and_watermark_stats():
    # the client's own uploads are always in ocr scope
    channel = make_channel(APOLLO, "Clinic")
    videos = [
        make_video(
            f"en{n:09d}",
            APOLLO,
            AS_OF - timedelta(days=2 + n),
            thumbnails={
                "high": ThumbnailRef(
                    url=f"https://i.ytimg.com/vi/en{n:09d}/{'missing' if n == 3 else 'hq'}.jpg"
                )
            },
        )
        for n in range(8)
    ]
    store = MemoryStore()
    deps = Deps(
        analytics_inputs=StaticSource(make_inputs([scoped(channel, ChannelRole.own)], videos)),
        analytics_store=store,
        image_source=Images(),
        thumbnail_text_reader=Reader(),
    )
    result = await EnrichStage().run(context(deps))
    assert result.artifact.computed == 7 and result.artifact.failed == [videos[3].video_id]
    assert ("clinic", 8) in store.recurring[APOLLO]
    traits = store.thumbnails[0]
    assert traits.text_present and traits.word_count == 3 and traits.text_box_count == 1
    assert traits.ocr_engine == "fake" and 0 <= traits.brightness <= 1
