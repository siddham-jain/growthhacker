from collections import Counter
from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import LabellingSettings
from app.evaluation.labelling.models import Population, PopulationVideo, UploadPoint
from app.evaluation.labelling.pack import render_label_pack
from app.evaluation.labelling.sampler import (
    OutlierSampleDesigner,
    allocate_band,
    largest_remainder,
    ratio_band,
    size_band,
)

NOW = datetime(2026, 11, 20, 12, tzinfo=UTC)
THRESHOLD = 3.0
SUBSCRIBERS = {"S0": None, "S1": 5_000, "S2": 50_000, "S3": 500_000, "S4": 5_000_000}
RATIOS = {"R1": 7.1234, "R2": 3.5, "R3": 2.2, "R4": 0.9}


def video(n: int, size: str, band: str, channel: int, segment: str = "long_form") -> PopulationVideo:
    return PopulationVideo(
        video_id=f"v{n:010d}",
        channel_id=f"UC{channel:022d}",
        channel_title=f"Channel {channel}",
        client_ids=("c1",),
        title=f"Video {n}",
        segment=segment,
        duration_s=600 if segment != "short_form" else 40,
        published_at=NOW - timedelta(days=20),
        age_days=20.0,
        views=10_000 + n,
        ratio=RATIOS[band],
        is_outlier=RATIOS[band] >= THRESHOLD,
        subscriber_count=SUBSCRIBERS[size],
        hidden_subscribers=size == "S0",
        thumbnail_url=f"https://i.ytimg.com/vi/v{n:010d}/hqdefault.jpg",
        oldest_api_at=NOW - timedelta(days=2),
    )


def population(per_cell: int = 12, sizes: tuple[str, ...] = ("S0", "S1", "S2", "S3", "S4")) -> Population:
    videos, n = [], 0
    for size in sizes:
        for band in RATIOS:
            for _ in range(per_cell):
                videos.append(video(n, size, band, channel=n // 2))
                n += 1
    for band in RATIOS:
        for _ in range(20):
            videos.append(video(n, "S2", band, channel=n // 2, segment="short_form"))
            n += 1
    return Population(
        observed_at=NOW,
        threshold=THRESHOLD,
        detector_version="det1",
        videos=tuple(videos),
        uploads={
            v.channel_id: (
                UploadPoint(
                    video_id=v.video_id,
                    title=v.title,
                    published_at=v.published_at,
                    views=v.views,
                    age_days=v.age_days,
                    segment=v.segment,
                ),
            )
            for v in videos
        },
    )


def test_bands() -> None:
    assert [ratio_band(r, 3.0) for r in (6.0, 3.0, 1.8, 1.79)] == ["R1", "R2", "R3", "R4"]
    assert size_band(video(1, "S0", "R1", 1)) == "S0"
    assert size_band(video(1, "S4", "R1", 1)) == "S4"


def test_largest_remainder_and_band_allocation_with_overflow() -> None:
    assert largest_remainder(30, {"R1": 15, "R2": 30, "R3": 25, "R4": 30}) == {
        "R1": 5,
        "R2": 9,
        "R3": 7,
        "R4": 9,
    }
    capacity = {"S0": 0, "S1": 1, "S2": 10, "S3": 10, "S4": 0}
    allocation = allocate_band(15, capacity, min_per_cell=2)
    assert allocation == {"S0": 0, "S1": 1, "S2": 9, "S3": 5, "S4": 0}
    assert sum(allocation.values()) == 15


def test_design_follows_allocation_and_records_inclusion_probabilities() -> None:
    settings = LabellingSettings()
    design = OutlierSampleDesigner(settings).design(population(), seed=3)
    per_band = Counter(
        task.stratum.split("|")[1] for task in design.tasks if not task.stratum.startswith("SH")
    )
    assert per_band == {"R1": 15, "R2": 30, "R3": 25, "R4": 30}
    shorts = [task for task in design.tasks if task.stratum.startswith("SH")]
    assert len(shorts) == settings.shorts_sample_size
    for task in design.tasks:
        assert task.inclusion_prob == pytest.approx(
            design.cell_sampled[task.stratum] / design.cell_population[task.stratum]
        )
    channels = Counter(task.video.channel_id for task in design.tasks)
    assert max(channels.values()) <= settings.max_per_channel
    assert all(n >= settings.min_per_cell for n in design.cell_sampled.values())


def test_design_is_seeded() -> None:
    first = OutlierSampleDesigner(LabellingSettings()).design(population(), seed=11)
    second = OutlierSampleDesigner(LabellingSettings()).design(population(), seed=11)
    assert [t.video.video_id for t in first.tasks] == [t.video.video_id for t in second.tasks]


def test_empty_size_bands_are_reported_and_reallocated() -> None:
    design = OutlierSampleDesigner(LabellingSettings()).design(
        population(per_cell=30, sizes=("S1", "S2")), seed=1
    )
    main = [task for task in design.tasks if not task.stratum.startswith("SH")]
    assert len(main) == 100
    assert {task.stratum.split("|")[0] for task in main} == {"S1", "S2"}
    assert "no S4 channels in the population" in design.notes


def test_label_pack_hides_detector_values() -> None:
    pop = population()
    design = OutlierSampleDesigner(LabellingSettings()).design(pop, seed=3)
    page = render_label_pack(
        design, pop, participant="mira", seed=3, batch_size=25, retention_days=28, now=NOW
    )
    assert "7.1234" not in page
    assert "detector" not in page
    assert "stratum" not in page
    assert design.tasks[0].video.title in page
    assert "<svg" in page and "views (log scale)" in page
    assert "anchor videos" in page
