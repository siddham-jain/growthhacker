"""scoring functions 12 uses to validate the outlier rule and the age-band proxy; 06 only exposes them."""

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace

from app.domain.analytics import AgeMethod, OutlierTier
from app.domain.common import FormatSegment
from app.services.analytics.ages import views_at_exact_age
from app.services.analytics.baselines import AgeFamily, AgeValue
from app.services.analytics.outliers import OUTLIER_TIERS
from app.services.analytics.patterns.stats import cohen_kappa, median, spearman_rho, wilson
from app.services.analytics.state import AssessmentBook, VideoState

SIMULATED_FIRST_SEEN_DAYS = (10, 20, 30)
VALIDATION_AGE = 7
MIN_TRACKED_AGE = 30


@dataclass(frozen=True, slots=True)
class ProxyAgreement:
    segment: FormatSegment
    first_seen_age: int
    n: int
    kappa: float | None
    spearman: float | None
    median_log_bias: float | None


def proxy_agreement(
    book: AssessmentBook, states: Iterable[VideoState], ages: Sequence[int] = SIMULATED_FIRST_SEEN_DAYS
) -> list[ProxyAgreement]:
    """simulates 'first seen at age a' on fully tracked videos and compares with their true day-7 flag."""
    gap = book.settings.ages.max_gap_days_for
    rows: dict[tuple[FormatSegment, int], list[tuple[bool, bool, float, float]]] = {}
    for state in sorted(states, key=VideoState.ordering_key):
        true = book.true_assessment(state, VALIDATION_AGE)
        if true is None or true.method is AgeMethod.owner_analytics or state.age_now < MIN_TRACKED_AGE:
            continue
        if true.tier is OutlierTier.insufficient:
            continue
        for age in ages:
            seen = views_at_exact_age(state.series, age, gap(age))
            if seen is None:
                continue
            facts = replace(
                book.facts_of(state.video_id),
                values={
                    (VALIDATION_AGE, AgeFamily.proxy): AgeValue(
                        round(seen), AgeMethod.age_band_proxy, age, book.as_of
                    )
                },
                latest_age_days=float(age),
            )
            built = book.builder.for_video(facts, VALIDATION_AGE, AgeFamily.proxy).baseline
            if not built.sufficient:
                continue
            tier, ratio, _ = book.assessor.rule.assess(round(seen), built)
            rows.setdefault((state.segment, age), []).append(
                (tier in OUTLIER_TIERS, true.tier in OUTLIER_TIERS, ratio, true.ratio)
            )
    report = []
    for (segment, age), items in sorted(rows.items()):
        proxy_flags, true_flags = [r[0] for r in items], [r[1] for r in items]
        proxy_ratios, true_ratios = [r[2] for r in items], [r[3] for r in items]
        logs = [math.log10(p / t) for p, t in zip(proxy_ratios, true_ratios, strict=True) if p > 0 and t > 0]
        report.append(
            ProxyAgreement(
                segment=segment,
                first_seen_age=age,
                n=len(items),
                kappa=cohen_kappa(proxy_flags, true_flags),
                spearman=spearman_rho(proxy_ratios, true_ratios),
                median_log_bias=median(logs),
            )
        )
    return report


@dataclass(frozen=True, slots=True)
class WeightedScore:
    precision: float | None
    recall: float | None
    precision_wilson: tuple[float, float]
    recall_wilson: tuple[float, float]
    f_half: float | None


def weighted_precision_recall(
    predicted: Sequence[bool], labelled: Sequence[bool], weights: Sequence[float]
) -> WeightedScore:
    # inverse-probability stratum weights make over-sampled strata count at their population share
    if not len(predicted) == len(labelled) == len(weights):
        raise ValueError("predicted, labelled and weights must have the same length")
    tp = sum(w for p, y, w in zip(predicted, labelled, weights, strict=True) if p and y)
    fp = sum(w for p, y, w in zip(predicted, labelled, weights, strict=True) if p and not y)
    fn = sum(w for p, y, w in zip(predicted, labelled, weights, strict=True) if not p and y)
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    predicted_n = sum(predicted)
    positives_n = sum(labelled)
    true_positives_n = sum(p and y for p, y in zip(predicted, labelled, strict=True))
    f_half = None
    if precision and recall:
        f_half = 1.25 * precision * recall / (0.25 * precision + recall)
    return WeightedScore(
        precision=precision,
        recall=recall,
        precision_wilson=wilson(true_positives_n, predicted_n),
        recall_wilson=wilson(true_positives_n, positives_n),
        f_half=f_half,
    )
