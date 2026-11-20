from datetime import timedelta

import pytest

from app.domain.analytics import GapCell
from app.services.recommend.evidence.index import EvidenceIndex, OwnVideo
from app.services.recommend.gaps import GapClassifier
from tests.recommend.factories import TOPIC, evidence
from tests.recommend.fixtures import AS_OF, channel_id

COMPETITORS = [channel_id("comp1"), channel_id("comp2")]


def own(days_ago: float, ratio: float | None) -> OwnVideo:
    return OwnVideo(
        video_id="ownvideo001",
        title="Own video",
        published_at=AS_OF - timedelta(days=days_ago),
        age_days=days_ago,
        format_key="long_form|3–8m",
        topic_ids=frozenset({TOPIC}),
        ratio=ratio,
        is_outlier=False,
        traits={},
    )


def index(own_videos: list[OwnVideo]) -> EvidenceIndex:
    videos = [
        evidence(1, 4.0, channel="comp1"),
        evidence(2, 3.5, channel="niche1"),
        evidence(3, 0.9, channel="comp2", topic_ids=frozenset()),
    ]
    return EvidenceIndex(
        window_days=30,
        as_of=AS_OF,
        videos=videos,
        own_videos=own_videos,
        excluded={},
        client_baselines={},
        comparison_age_days=7,
        all_titles=[],
    )


CELLS = {COMPETITORS[0]: GapCell(n_videos=2, n_outliers=1, best_ratio=4.0, latest_published_at=AS_OF)}
CLASSIFIER = GapClassifier(half_life_days=180, stale_days=180, underperforming_ratio=0.8)


@pytest.mark.parametrize(
    ("own_videos", "gap_type"),
    [
        ([], "uncovered"),
        ([own(250, 1.2)], "stale"),
        ([own(40, 0.5)], "underperforming"),
        ([own(40, 1.5)], "none"),
    ],
)
def test_gap_types(own_videos: list[OwnVideo], gap_type: str) -> None:
    row = CLASSIFIER.classify(TOPIC, index(own_videos), CELLS, COMPETITORS, window_outliers=2)

    assert row.gap_type == gap_type
    assert row.competitors_covering == 1 and row.competitors_total == 2
    assert row.niche_outliers_matching == 2


def test_gap_size_shrinks_with_recent_client_coverage() -> None:
    uncovered = CLASSIFIER.classify(TOPIC, index([]), CELLS, COMPETITORS, window_outliers=2)
    recent = CLASSIFIER.classify(TOPIC, index([own(5, 1.5)]), CELLS, COMPETITORS, window_outliers=2)
    weak = CLASSIFIER.classify(TOPIC, index([own(5, 0.5)]), CELLS, COMPETITORS, window_outliers=2)

    assert uncovered.gap_size > weak.gap_size > recent.gap_size
    assert uncovered.gap_size == pytest.approx(0.5 * 0.5 + 0.5 * 1.0)


def test_coverage_falls_back_to_the_index_without_a_gap_matrix() -> None:
    row = CLASSIFIER.classify(TOPIC, index([]), {}, COMPETITORS, window_outliers=2)

    assert row.competitors_covering == 1
