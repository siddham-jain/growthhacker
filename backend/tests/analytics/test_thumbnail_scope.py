import threading
import time
from datetime import timedelta

import numpy as np
import pytest
from pydantic import ValidationError

from app.core.config import AnalyticsSettings
from app.domain.analytics import OutlierTier
from app.domain.common import ChannelRole
from app.domain.youtube import ThumbnailRef
from app.pipeline.stages.analytics import EnrichStage
from app.services.analytics.engine import AnalyticsEngine
from app.services.analytics.patterns.contingency import MiningUnit
from app.services.analytics.patterns.miner import main_effect_tests
from app.services.analytics.settings import AnalyticsEngineSettings
from app.services.analytics.thumbnails.analyzer import (
    OCR_SKIPPED,
    TRAITS_VERSION,
    ThumbnailTraitBuilder,
    has_text_traits,
)
from app.services.analytics.thumbnails.selection import in_control_sample, ocr_scope, window_videos
from app.services.analytics.thumbnails.text import TextBox
from app.services.analytics.traits.thumbnail import ThumbnailPart, ThumbnailTraitExtractor
from app.services.recommend.evidence.traits import flatten_traits
from tests.analytics.apollo_fixture import APOLLO, COMPETITORS
from tests.analytics.factories import AS_OF, make_channel, make_inputs, make_video, scoped
from tests.analytics.test_golden import build_inputs
from tests.analytics.test_stage import Deps, Images, MemoryStore, StaticSource, context
from tests.analytics.test_thumbnails_and_comments import FakeReader, box, encoded, solid
from tests.conftest import make_settings
from tests.recommend.fixtures import VideoSpec, video_traits

SETTINGS = AnalyticsEngineSettings()
THUMBS = SETTINGS.thumbnails


def test_scope_keeps_every_breakout_and_own_upload_and_skips_the_rest():
    inputs = build_inputs()
    scope = ocr_scope(inputs, SETTINGS, AS_OF, control_share=0.0)
    window = {v.video_id for v in window_videos(inputs, SETTINGS, AS_OF)}
    result = AnalyticsEngine(SETTINGS).run(inputs, AS_OF)
    breakouts = {
        vid
        for by_video in result.metrics.values()
        for vid, metrics in by_video.items()
        if metrics.outlier is not None
        and metrics.outlier.tier in (OutlierTier.breakout, OutlierTier.outlier, OutlierTier.strong_outlier)
    }
    listed = {o.video_id for w in result.windows.values() for items in w.outliers.values() for o in items}
    references = {
        vid
        for w in result.windows.values()
        for lifts in w.lifts.values()
        for x in lifts
        for vid in x.reference_video_ids
    }
    own = {v.video_id for v in inputs.videos if v.channel_id == APOLLO and v.video_id in window}
    assert listed and breakouts and own
    assert listed | breakouts | references | own <= scope <= window
    assert len(scope) < len(window)


def test_control_sample_is_stable_and_proportional():
    ids = [f"vid{n:08d}" for n in range(4000)]
    picked = [vid for vid in ids if in_control_sample(vid, 0.2)]
    assert picked == [vid for vid in ids if in_control_sample(vid, 0.2)]
    assert 0.17 < len(picked) / len(ids) < 0.23
    assert not any(in_control_sample(vid, 0.0) for vid in ids)
    assert all(in_control_sample(vid, 1.0) for vid in ids)
    inputs = build_inputs()
    assert ocr_scope(inputs, SETTINGS, AS_OF, 1.0) == {
        v.video_id for v in window_videos(inputs, SETTINGS, AS_OF)
    }


def pixel_only_and_read():
    reader = FakeReader([box("KNEE PAIN FIXED", 0.95, 50, 100)])
    builder = ThumbnailTraitBuilder(reader, THUMBS)
    image = encoded(solid(360, 640, (240, 240, 240)))
    skipped = builder.read(image, devanagari_hint=False, ocr=False)
    read = builder.read(image, devanagari_hint=False)
    return (
        reader,
        builder.traits("vid00000001", skipped, frozenset(), frozenset(), AS_OF),
        builder.traits("vid00000002", read, frozenset(), frozenset(), AS_OF),
    )


def test_pixel_only_traits_keep_pixels_and_leave_text_unknown():
    reader, skipped, read = pixel_only_and_read()
    assert len(reader.calls) == 1
    assert skipped.ocr_engine == OCR_SKIPPED and not has_text_traits(skipped) and has_text_traits(read)
    assert skipped.word_count is None and skipped.word_count_band is None
    assert skipped.script_detected == "unknown" and skipped.ocr_confidence_mean is None
    assert skipped.brightness_band == read.brightness_band and skipped.contrast_band == read.contrast_band


def test_skipped_text_is_missing_not_no_text_in_lift_mining():
    _, skipped, read = pixel_only_and_read()
    extractor = ThumbnailTraitExtractor()
    skipped_features = extractor.mined(ThumbnailPart(skipped, degenerate=False))
    assert "thumbnail.text_present" not in skipped_features
    assert "thumbnail.brand_text_only" not in skipped_features
    assert "thumbnail.brightness_band" in skipped_features
    # outliers were read and show text; non-outliers were skipped, whose placeholder says no text
    units = [
        MiningUnit(
            video_id=f"vid{n:08d}",
            channel_id=COMPETITORS[n % 3],
            is_outlier=n < 10,
            ratio=4.0 if n < 10 else 1.0,
            published_at=AS_OF - timedelta(days=n % 60),
            features=extractor.mined(ThumbnailPart(read if n < 10 else skipped, degenerate=False)),
        )
        for n in range(60)
    ]
    tests = {(t.key, t.value): t.table for t in main_effect_tests(units)}
    text = tests[("thumbnail.text_present", "true")]
    assert len(text.with_units) == 10 and not text.without_units
    assert len(tests[("thumbnail.brightness_band", read.brightness_band)].with_units) == 60


def test_recommend_evidence_never_reads_skipped_text_as_false():
    _, skipped, read = pixel_only_and_read()
    traits = video_traits(VideoSpec("a1", "apollo", "Knee pain", 3, 1.0))
    flat_skipped = flatten_traits(traits.model_copy(update={"thumbnail": skipped}))
    flat_read = flatten_traits(traits.model_copy(update={"thumbnail": read}))
    assert "thumbnail.text_present" not in flat_skipped and "thumbnail.brightness_band" in flat_skipped
    assert flat_read["thumbnail.text_present"] == "true"


class SlowReader:
    engine, model = "fake", "fake-1"

    def __init__(self) -> None:
        self.active = 0
        self.peak = 0
        self.calls = 0
        self.lock = threading.Lock()

    def read(self, image: np.ndarray, script):
        with self.lock:
            self.active += 1
            # one latin pass per thumbnail; a devanagari pass may follow
            self.calls += script == "latin"
            self.peak = max(self.peak, self.active)
        time.sleep(0.03)
        with self.lock:
            self.active -= 1
        return [TextBox(((40, 100), (400, 100), (400, 200), (40, 200)), "KNEE PAIN FIXED", 0.93)]


class FreshStore(MemoryStore):
    async def recurring_text(self, channel_id, since):
        return frozenset()


def own_videos(count: int):
    return [
        make_video(
            f"ow{n:09d}",
            APOLLO,
            AS_OF - timedelta(days=2 + n),
            thumbnails={"high": ThumbnailRef(url=f"https://i.ytimg.com/vi/ow{n:09d}/hq.jpg")},
        )
        for n in range(count)
    ]


def enrich_deps(videos, reader, store, workers: int, **inputs_overrides) -> Deps:
    settings = make_settings(analytics={"thumbnail_ocr_workers": workers})
    channel = scoped(make_channel(APOLLO, "Clinic"), ChannelRole.own)
    return Deps(
        analytics_inputs=StaticSource(make_inputs([channel], videos, **inputs_overrides)),
        analytics_store=store,
        image_source=Images(),
        thumbnail_text_reader=reader,
        settings=settings,
    )


async def test_ocr_runs_concurrently_within_the_worker_bound():
    reader = SlowReader()
    deps = enrich_deps(own_videos(12), reader, FreshStore(), workers=3)
    result = await EnrichStage().run(context(deps))
    assert result.counts["thumbnails_ocr"] == 12 and reader.calls == 12
    assert 1 < reader.peak <= 3


async def test_cache_hits_skip_download_and_ocr():
    videos = own_videos(4)
    _, skipped, read = pixel_only_and_read()
    cached = {
        videos[0].video_id: read.model_copy(update={"video_id": videos[0].video_id}),
        videos[1].video_id: read.model_copy(update={"video_id": videos[1].video_id, "traits_version": "old"}),
        # pixel-only row for an own upload, which is now in scope, so its text is read
        videos[2].video_id: skipped.model_copy(update={"video_id": videos[2].video_id}),
    }
    reader, store = SlowReader(), FreshStore()
    deps = enrich_deps(videos, reader, store, workers=2, thumbnails=cached)
    result = await EnrichStage().run(context(deps))
    fetched = {url.split("/")[4] for url in deps.image_source.calls}
    assert fetched == {v.video_id for v in videos[1:]}
    assert result.counts["thumbnails_reused"] == 1 and result.counts["thumbnails_computed"] == 3
    assert reader.calls == 3 and all(has_text_traits(t) for t in store.thumbnails)
    assert all(t.traits_version == TRAITS_VERSION for t in store.thumbnails)


async def test_out_of_scope_videos_get_pixel_traits_without_ocr():
    videos = [
        make_video(
            f"cp{n:09d}",
            COMPETITORS[0],
            AS_OF - timedelta(days=2 + n),
            thumbnails={"high": ThumbnailRef(url=f"https://i.ytimg.com/vi/cp{n:09d}/hq.jpg")},
        )
        for n in range(6)
    ]
    reader, store = SlowReader(), MemoryStore()
    settings = make_settings(analytics={"thumbnail_ocr_control_share": 0.0})
    channel = scoped(make_channel(COMPETITORS[0], "Rival"), ChannelRole.competitor)
    deps = Deps(
        analytics_inputs=StaticSource(make_inputs([channel], videos)),
        analytics_store=store,
        image_source=Images(),
        thumbnail_text_reader=reader,
        settings=settings,
    )
    result = await EnrichStage().run(context(deps))
    assert result.counts["thumbnails_computed"] == 6 and result.counts["thumbnails_ocr"] == 0
    assert reader.calls == 0 and not any(has_text_traits(t) for t in store.thumbnails)
    assert store.recurring[COMPETITORS[0]] == {}


@pytest.mark.parametrize("workers", [0, -1])
def test_worker_count_must_be_positive(workers):
    with pytest.raises(ValidationError):
        AnalyticsSettings(thumbnail_ocr_workers=workers)
