from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.domain.common import FormatSegment
from app.services.recommend.evidence.index import EvidenceIndexBuilder

AS_OF = datetime(2026, 11, 20, tzinfo=UTC)


def baseline(baseline_id: str, median: float, n: int, method: str, as_of: datetime = AS_OF):
    return SimpleNamespace(
        baseline_id=baseline_id,
        metric="views_at_age",
        age_days=7,
        as_of=as_of,
        segment=FormatSegment.long_form,
        median=median,
        n=n,
        sufficient=n >= 5,
        method=method,
    )


def chosen(*baselines) -> float:
    report = SimpleNamespace(baselines=list(baselines))
    return EvidenceIndexBuilder._client_baselines(report, 7, [], {})[FormatSegment.long_form].views_at_age


def test_true_age_beats_proxy_whatever_order_the_report_lists_them() -> None:
    true_age = baseline("bl_0a", 2_000, 30, "observed")
    proxy = baseline("bl_ff", 48_000, 2, "age_band_proxy")
    assert chosen(true_age, proxy) == chosen(proxy, true_age) == 2_000


def test_then_the_larger_sample_and_then_the_newest() -> None:
    small = baseline("bl_1", 1_000, 6, "observed")
    large = baseline("bl_2", 3_000, 25, "interpolated")
    assert chosen(large, small) == chosen(small, large) == 3_000
    older = baseline("bl_3", 1_500, 25, "observed", AS_OF - timedelta(days=1))
    assert chosen(large, older) == chosen(older, large) == 3_000
